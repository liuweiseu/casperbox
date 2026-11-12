# casperbox toolflow plugin: $name

Created from `$template` by `casperbox plugin new`. casperbox builds this version exactly like a
built-in one: `casperbox init --toolflow $name`, then `casperbox up`.

## Files

| File | Required | Description |
|---|---|---|
| `toolflow.toml` | yes | Version definition. Every key is passed to the Dockerfile as a build arg. `TOOLFLOW_VERSION` must be the directory name (`$name`). |
| `pkgs.list` | yes | Extra apt packages, one per line; `#` starts a comment, a trailing `?` marks an optional package. May be empty. |
| `patch/*.sh` | no | Fixes run with bash, as root, in file name order (`10-a.sh` before `50-b.sh`), at the end of the build. The apt indexes are updated before, so a script may `apt-get install`. A failing script fails the build. Other files in `patch/` are ignored. |
| `Dockerfile` | no | Replaces the shared toolflow Dockerfile. The build context contains `common/`, `toolflow/` and `versions/$name/`. |
| `README.md` | no | This file; not used by the build. |

## toolflow.toml keys

| Key | Description |
|---|---|
| `BASE_IMAGE` | Base image, fully qualified (e.g. `docker.io/library/ubuntu:22.04`) |
| `VIVADO_VERSION` | `vivado.dir` must contain `Vivado/<VIVADO_VERSION>/settings64.sh` (up to 2024.x) or `<VIVADO_VERSION>/Vivado/settings64.sh` (from 2025.1), or be the Vivado directory itself |
| `MINICONDA_INSTALLER`, `PYTHON_VERSION`, `CONDA_ENV_NAME` | conda env |
| `MLIB_DEVEL_REPO`, `MLIB_DEVEL_BRANCH` | mlib_devel, cloned to `~/toolflow/mlib_devel` |
| `DEVICE_TREE_XLNX_REPO`, `DEVICE_TREE_XLNX_BRANCH` | device-tree-xlnx, usually `xlnx_rel_v<Vivado version>` |
| `GIT_CLONE_DEPTH` | git clone depth (0 = full history) |
| `SCILAB_VERSION`, `SCILAB_URL` | Scilab tarball |

## Checklist

1. Update `toolflow.toml` for the new Vivado version, base image and branches.
2. Review `pkgs.list` and `patch/`: they were copied from `$template` and may not apply to this
   version (e.g. Vivado's `scripts/installLibs.sh` lists different packages per release).
3. `casperbox plugin check $name` validates the files without building.
4. `casperbox init --toolflow $name`, then `casperbox up`. Any change here makes `up` rebuild the image.

Plugins are searched in the directories of `CASPERBOX_PLUGIN_PATH` (separated by `:`) and in
`~/.config/casperbox/versions`. A plugin may not have the name of a built-in version. Patch scripts
end up in the image: keep shared plugin directories writable only by their maintainers.
