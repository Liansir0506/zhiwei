"""Create a private local profile with unique credentials; never overwrite a profile."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets


def initialize(environment: str, root: Path | None = None) -> Path:
    if environment not in {'dev', 'test', 'demo'}:
        raise ValueError('environment must be dev, test or demo')
    root = (root or Path(__file__).resolve().parents[1]).resolve()
    target = root / f'.env.{environment}'
    template = root / 'config' / 'environments' / f'{environment}.env.example'
    content = template.read_text(encoding='utf-8')
    for placeholder in ('REPLACE_DATABASE_PASSWORD', 'REPLACE_ROOT_PASSWORD',
                        'REPLACE_MINIO_ACCESS_KEY', 'REPLACE_MINIO_SECRET_KEY'):
        if placeholder not in content:
            raise ValueError('Incomplete environment template')
        # 20-character access ID and 32-character secret also fit older S3 tools.
        size = 15 if placeholder == 'REPLACE_MINIO_ACCESS_KEY' else 24 if placeholder == 'REPLACE_MINIO_SECRET_KEY' else 32
        content = content.replace(placeholder, secrets.token_urlsafe(size))
    # O_EXCL prevents accidental replacement; 0600 also protects POSIX clones.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write(content)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', choices=('dev', 'test', 'demo'), default='dev')
    args = parser.parse_args()
    try:
        path = initialize(args.environment)
    except FileExistsError:
        parser.exit(1, 'Configuration already exists; it was not overwritten.\n')
    print(f'Created {path.name}; credentials are stored locally and must not be committed.')


if __name__ == '__main__':
    main()
