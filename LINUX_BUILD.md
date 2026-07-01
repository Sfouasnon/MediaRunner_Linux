# MediaRunner Linux Build

This folder is a Linux build workspace copied from the current MediaRunner V2 source. The original macOS source tree is unchanged.

## Current status

- Build scripts are ready for a Linux host or Docker-capable build machine.
- PyInstaller must run on Linux to produce a Linux executable.

## Native Linux build

Run on a Linux desktop or build VM:

```bash
cd /path/to/MediaRunner_Linux
chmod +x build_linux.sh
./build_linux.sh
```

Output:

```text
dist/MediaRunner/MediaRunner
```

## Docker build

Run on a Docker-capable host:

```bash
cd /path/to/MediaRunner_Linux
chmod +x build_linux_container.sh
./build_linux_container.sh
```

By default the Docker script builds `linux/amd64`. Override with:

```bash
PLATFORM=linux/arm64 ./build_linux_container.sh
```

Output:

```text
dist-linux/MediaRunner/MediaRunner
dist/MediaRunner-linux-amd64.tar.gz
```

## Linux runtime notes

Multi-magazine ingest workflow:

```text
Settings -> Linux Ingest -> Destination Throughput Test
Offload -> Source Mode: Multi-Mag -> Detect Mounted or Add Magazine
```

The throughput test writes temporary files under `.mediarunner_throughput_test`,
fsyncs them, reports aggregate stream throughput, then removes the test folder.
It saves a destination profile in the local MediaRunner config. Multi-Mag
Offload applies matching destination profiles automatically; with multiple
destinations selected, the most conservative profile caps magazine concurrency.

Camera-array FTP ingest workflow:

```text
Networking -> FTP Download Workers
FTP -> Camera Array -> Start Download
```

The old large-array hard cap is removed. FTP camera pulls now use the configured
worker count, capped by active online cameras. For 36 tethered cameras, tune this
against the switch and target storage instead of relying on a baked-in value.

Metadata extraction:

```text
Settings -> Metadata workers
Metadata -> Extract Metadata
```

REDline, ffprobe, and ExifTool processing now runs in parallel per file, then
writes the master CSV/report once after all rows complete.

The first Linux build should be validated on the target distro with:

```bash
python verify_install.py
python validation/run_validation_suite.py --profile extended --work-dir ./validation_runs/linux_extended_001
```

Real release signoff still needs a GUI smoke test, a local offload test, metadata tool detection with Linux `ffmpeg`/`ffprobe`/`exiftool`, and real RED camera FTP/RCP2 testing.
