"""Cache official WFDB files only (no duplicate CSV export), with SHA256 checks."""
import argparse
import hashlib
from pathlib import Path
import time
from urllib.request import Request, urlopen

SOURCE = 'https://physionet.org/files/pulse-transit-time-ppg/1.1.0/'


def download(name, destination):
    partial = destination.with_suffix(destination.suffix + '.part')
    for attempt in range(3):
        try:
            request = Request(SOURCE + name, headers={'User-Agent': 'HALO-PPG-Research/1.0'})
            with urlopen(request, timeout=60) as response, partial.open('wb') as stream:
                while chunk := response.read(1024 * 1024):
                    stream.write(chunk)
            partial.replace(destination)
            return
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path(__file__).resolve().parent / 'data')
    args = parser.parse_args()
    args.data.mkdir(parents=True, exist_ok=True)
    for name in ('RECORDS', 'SHA256SUMS.txt'):
        download(name, args.data / name)
    checksums = {}
    for line in (args.data / 'SHA256SUMS.txt').read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        checksums[name.lstrip('*').removeprefix('./')] = digest
    records = (args.data / 'RECORDS').read_text().split()
    for index, record in enumerate(records, 1):
        # Reject path components before using server-supplied manifest names.
        if '/' in record or '\\' in record or not record.startswith('s'):
            raise ValueError(f'Invalid manifest record: {record}')
        print(f'[{index}/{len(records)}] {record}', flush=True)
        for extension in ('hea', 'dat', 'atr'):
            name = f'{record}.{extension}'
            target = args.data / name
            expected = checksums[name]
            if target.exists():
                with target.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() == expected:
                        continue
            download(name, target)
            with target.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    raise ValueError(f'Checksum mismatch after download: {name}')
    print(f'All {len(records)} recordings cached and verified in {args.data.resolve()}')


if __name__ == '__main__':
    main()
