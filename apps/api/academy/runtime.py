"""Fixed service commands used by the dedicated Core recipe."""
import argparse
import os


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["api", "migrate"])
    args = parser.parse_args()
    if args.command == "migrate":
        from .migrate import migrate
        migrate()
    else:
        import uvicorn
        uvicorn.run("academy.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()
