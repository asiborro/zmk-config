"""Record exact revisions for the active dependencies used by this build."""

import sys

import yaml
from west.manifest import Manifest


manifest = Manifest.from_file()
frozen = manifest.as_dict()
# West's --freeze also visits disabled optional groups, which west update
# deliberately does not clone. Freeze only active dependencies.
frozen["manifest"]["projects"] = [
    {**project.as_dict(), "revision": project.sha("manifest-rev")}
    for project in manifest.projects[1:]
    if manifest.is_active(project)
]
yaml.safe_dump(frozen, sys.stdout, sort_keys=False)
