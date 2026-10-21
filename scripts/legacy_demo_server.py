"""Owned background server for legacy_demo_runtime; citation mode only."""
import argparse
import uvicorn
from scripts.legacy_demo_runtime import ROOT, VERSION, apply_profile, digest, read


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()
    from pathlib import Path
    if Path(args.profile).resolve() != ROOT / "profile.json":
        raise ValueError("requires owned runtime profile")
    state = read(ROOT / "state.json")
    if args.instance != state["instance"]:
        raise ValueError("runtime instance mismatch")
    if digest(Path(args.profile)) != state["profile_sha256"]:
        raise ValueError("runtime profile changed before server start")
    settings = apply_profile(read(args.profile))
    from app import create_app
    app = create_app()

    @app.get("/demo/runtime")
    def runtime_identity():
        return {"instance": args.instance, "version": VERSION, "generation_enabled": False}

    uvicorn.run(app, host="127.0.0.1", port=settings.api_port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
