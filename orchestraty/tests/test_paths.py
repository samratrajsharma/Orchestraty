import os

from orchestraty.paths import State, state_root


def test_orch_home_overrides_everything(tmp_path):
    env = {"ORCH_HOME": str(tmp_path / "x"), "USERPROFILE": "C:\\u", "XDG_STATE_HOME": "/s"}
    assert state_root(env, windows=True) == str(tmp_path / "x")
    assert state_root(env, windows=False) == str(tmp_path / "x")


def test_windows_default_is_in_the_profile_not_appdata():
    env = {"USERPROFILE": os.path.join("C:", os.sep, "Users", "u"), "LOCALAPPDATA": "C:\\Users\\u\\AppData\\Local"}
    root = state_root(env, windows=True)
    assert root == os.path.join(env["USERPROFILE"], ".orchestraty")
    assert "AppData" not in root


def test_posix_default_follows_xdg_state_home():
    assert state_root({"HOME": "/home/u"}, windows=False) == os.path.join("/home/u", ".local", "state", "orchestraty")
    xdg = os.path.abspath(os.path.join(os.sep, "xdg", "state"))
    assert state_root({"HOME": "/home/u", "XDG_STATE_HOME": xdg}, windows=False) == os.path.join(xdg, "orchestraty")
    # A relative XDG_STATE_HOME is ignored, as the XDG rules say.
    assert state_root({"HOME": "/home/u", "XDG_STATE_HOME": "rel"}, windows=False) == os.path.join(
        "/home/u", ".local", "state", "orchestraty")


def test_the_tests_never_see_the_real_state_folder(tmp_path):
    assert State().root == str(tmp_path / "state")


def test_state_parts(tmp_path):
    s = State(str(tmp_path / "st"))
    assert s.hook_script == str(tmp_path / "st" / "hooks" / "hook.py")
    assert s.spool == str(tmp_path / "st" / "spool")
    assert s.backups == str(tmp_path / "st" / "backups")
