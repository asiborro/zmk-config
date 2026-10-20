#!/usr/bin/env python3
"""Deploy the darkblue and roseblue jobs/dashboards to the monitoring Pi."""

import argparse
import json
from pathlib import Path
import shlex
import subprocess

REMOTE = r'''
import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

payload = json.load(sys.stdin)
for content in payload['dashboards'].values():
    json.loads(content)
config = Path('/opt/monitoring/prometheus.yml')
dashboard_dir = Path('/opt/monitoring/grafana/dashboards')
original = config.read_text()
begin = '  # BEGIN peamk-battery (managed by zmk-config/monitoring)\n'
end = '  # END peamk-battery\n'
if begin in original and end in original:
    before, rest = original.split(begin, 1)
    _, after = rest.split(end, 1)
    candidate = before + begin + payload['job'] + end + after
else:
    if any('job_name: ' + job in original for job in ('peamk-battery', 'darkblue', 'roseblue')):
        raise SystemExit('Existing unmarked keyboard job: review it before replacing.')
    candidate = original.rstrip() + '\n' + begin + payload['job'] + end

# Validate the full config with the exact running Prometheus image before writing.
image = subprocess.check_output(['docker', 'inspect', 'monitoring-prometheus-1',
                                 '--format', '{{.Image}}'], text=True).strip()
with tempfile.TemporaryDirectory(prefix='peamk-prometheus-') as directory:
    staged = Path(directory) / 'prometheus.yml'
    staged.write_text(candidate)
    staged.chmod(0o644)
    subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', '/bin/promtool',
                    '-v', f'{staged}:/etc/prometheus/prometheus.yml:ro', image,
                    'check', 'config', '/etc/prometheus/prometheus.yml'], check=True)

stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')

def activate_config(expected):
    mounted = subprocess.check_output(['docker', 'exec', 'monitoring-prometheus-1',
                                       'cat', '/etc/prometheus/prometheus.yml'], text=True)
    if mounted != expected:
        # A previous deployment may have replaced the host file after Docker
        # mounted it. Restarting refreshes that bind mount; HUP alone cannot.
        print('Refreshing the stale Prometheus configuration mount', flush=True)
        subprocess.run(['docker', 'restart', '--time', '30', 'monitoring-prometheus-1'], check=True)
    else:
        subprocess.run(['docker', 'kill', '--signal=HUP', 'monitoring-prometheus-1'], check=True)

if candidate != original:
    if config.read_text() != original:
        raise SystemExit('Prometheus configuration changed concurrently; run again.')
    backup = config.with_name(f'prometheus.yml.before-peamk-{stamp}')
    shutil.copy2(config, backup)
    # Preserve the inode: this individual file is bind-mounted into the container.
    config.write_text(candidate)
    try:
        activate_config(candidate)
        loaded = False
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status/config', timeout=3) as response:
                    active = json.load(response)['data']['yaml']
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.5)
                continue
            if all('job_name: ' + job in active for job in ('darkblue', 'roseblue')):
                loaded = True
                break
            time.sleep(0.5)
        if not loaded:
            raise RuntimeError('New job did not appear in the active configuration')
    except Exception:
        config.write_text(original)
        activate_config(original)
        raise
    print(f'Prometheus reloaded; backup: {backup}')
else:
    print('Prometheus job already current')

for name, content in payload['dashboards'].items():
    if name not in ('darkblue.json', 'roseblue.json'):
        raise SystemExit('Unexpected dashboard filename')
    # Keep the original provisioned file/UID so bookmarks and history survive.
    dashboard = dashboard_dir / ('peamk-battery.json' if name == 'darkblue.json' else name)
    if dashboard.exists() and dashboard.read_text() != content:
        shutil.copy2(dashboard, dashboard.with_name(f'{name}.before-{stamp}.bak'))
    dashboard.write_text(content)
    dashboard.chmod(0o644)
    print(f'Dashboard installed: {dashboard}; Grafana reloads it within 30 seconds')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", nargs="?", default="ci4.test")
    args = parser.parse_args()
    directory = Path(__file__).parent
    payload = {"job": (directory / "prometheus-job.yml").read_text(),
               "dashboards": {name + ".json": (directory / (name + ".json")).read_text()
                              for name in ("darkblue", "roseblue")}}
    subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", args.host,
                    "sudo -n python3 -c " + shlex.quote(REMOTE)], input=json.dumps(payload), text=True, check=True)


if __name__ == "__main__":
    main()
