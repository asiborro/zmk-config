"""Record exact revisions for the active dependencies used by this build."""

import sys

import yaml
from west.manifest import Manifest

from pmw3610_patch import patch_metadata
from paw3395_patch import patch_metadata as paw_patch_metadata


manifest = Manifest.from_file()
frozen = manifest.as_dict()
# West's --freeze also visits disabled optional groups, which west update
# deliberately does not clone. Freeze only active dependencies.
frozen["manifest"]["projects"] = [
    {**project.as_dict(), "revision": project.sha("manifest-rev")}
    for project in manifest.projects[1:]
    if manifest.is_active(project)
]
for project in manifest.projects[1:]:
    helpers = {"zmk-pmw3610-driver": patch_metadata, "zmk-paw3395-driver": paw_patch_metadata}
    if project.name in helpers and manifest.is_active(project):
        patches = helpers[project.name](project.abspath)
        if patches:
            entry = next(item for item in frozen["manifest"]["projects"]
                         if item["name"] == project.name)
            entry.setdefault("userdata", {})["local-patches"] = patches
yaml.safe_dump(frozen, sys.stdout, sort_keys=False)
