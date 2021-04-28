# SPDX-License-Identifier: MIT

import importlib
import os
import subprocess
import tempfile

GIT_HASH_REGEX = r"^[0-9a-f]{5,40}$"

DATA_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), "data")


def strings_with_substring(strings, substring):
    return [string for string in strings if substring in string]


def import_path(path):
    """Imports python script as a module

    :param path: Path to python script to import
    :returns: imported module object
    """
    module_name = os.path.basename(path).replace("-", "_")
    spec = importlib.util.spec_from_loader(
        module_name, importlib.machinery.SourceFileLoader(module_name, path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # sys.modules[module_name] = module
    return module


def run_cmds(cmds):
    cwd = None
    for cmd in cmds:
        if cmd[0] == "cd":
            cwd = cmd[1]
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        out, err = proc.communicate()


def setup_test_repo(git_repo_dir, cfg_file=None):
    clone_dirobj = tempfile.TemporaryDirectory()
    clone_dir = clone_dirobj.name

    # setup a simple bare repo containing a README and a optional config file
    cmds = [
        ["cd", "/tmp"],
        ["git", "init", "--bare", git_repo_dir],
        ["rm", "-rf", clone_dir],
        ["git", "clone", git_repo_dir, clone_dir],
        ["cd", clone_dir],
        ["git", "config", "user.name", "John Doe"],
        ["git", "config", "user.email", "jdoe@example.com"],
        ["bash", "-c", "echo test > README"],
    ]
    if cfg_file:
        if cfg_file.startswith(("---\n", "configuration:\n")):
            tf = tempfile.NamedTemporaryFile(mode="w")
            tf.write(cfg_file)
            tf.flush()
            cfg_file = tf.name
        cmds.extend(
            [
                ["cp", cfg_file, "distrobaker.yaml"],
            ]
        )
    cmds.extend(
        [
            ["git", "add", "."],
            ["git", "commit", "-m", "Initial commit"],
            ["git", "push"],
            ["cd", git_repo_dir],
            ["git", "branch", "-m", "main"],
        ]
    )
    run_cmds(cmds)


def last_commit(repodir):
    cmd = ["git", "rev-parse", "--verify", "HEAD"]
    proc = subprocess.Popen(
        cmd, cwd=repodir, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    out, err = proc.communicate()
    return out.rstrip()
