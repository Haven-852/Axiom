import uvicorn

from ase.app import create_app
from ase.config import Settings


def main() -> None:
    settings = Settings()
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=settings.api_port)


if __name__ == "__main__":
    main()
