# hmsbox-headless

Docker container for running HEC-HMS hydrological simulations in headless mode.

## Image Size

~1.88GB - Primarily HMS binaries, libraries, and bundled Java runtime. Image size cannot be significantly reduced without removing essential HMS components.

## Supported Versions

**Current Default:**
- HMS 4.14-beta.1

**Stable Releases (Tested & Working):**
- HMS 4.13
- HMS 4.12

**Beta Versions (Tested & Working):**
- HMS 4.13-beta.6
- HMS 4.14-beta.1

**Legacy (Not Tested):**
- HMS 4.11, 4.9, 4.10 (available but not actively supported)

## Usage

Run an HMS simulation by passing the HMS project file path and simulation name as arguments:

```bash
docker run -v /path/to/models:/mnt/model hmsbox-headless:4.14-beta.1 /mnt/model/project.hms SimulationName
```

### Arguments

- `filepath`: Path to the HMS project file (`.hms`)
- `simname`: Name of the simulation/run to execute within the project

### Volume Mounts

Mount your HMS model files at `/mnt/model` in the container. The container expects to find `.hms` project files and all associated data files at this location.

**Important:** Files mounted into the container are automatically configured with appropriate permissions for the HMS user (uid 1000) to ensure write access during simulation runs.

## Building

Build the Docker image with a specific HMS version:

```bash
# Build with default version (4.14-beta.1)
docker build -t hmsbox-headless:4.14-beta.1 .

# Build with specific versions
docker build --build-arg HMS_VERSION=4.12 -t hmsbox-headless:4.12 .
docker build --build-arg HMS_VERSION=4.13 -t hmsbox-headless:4.13 .
docker build --build-arg HMS_VERSION=4.13-beta.6 -t hmsbox-headless:4.13-beta.6 .
docker build --build-arg HMS_VERSION=4.14-beta.1 -t hmsbox-headless:4.14-beta.1 .
```

The build process:
1. Compiles the Java HMS runner application using reflection-based API detection
2. Downloads HEC-HMS binaries and dependencies for the specified version (excludes samples/docs)
3. Creates a minimal production image with all required libraries and non-root user execution
4. Single `RunHMS` class supports all versions through runtime API detection

## Logging

The container outputs structured JSON logs for easy parsing and integration with logging systems:

```json
{"ts":"2026-05-09T12:08:25.711","level":"INFO","message":"Starting HMS simulation: model='/mnt/model/watershed.hms', simulation='Lookback'"}
{"ts":"2026-05-09T12:09:47.595","level":"INFO","message":"Completed HMS simulation: model='/mnt/model/watershed.hms', simulation='Lookback'"}
```

**Log Fields:**
- `ts`: ISO 8601 timestamp with millisecond precision (UTC, no Z suffix)
- `level`: Log level (INFO, WARNING, SEVERE)
- `message`: Descriptive message

**Log Levels:**
- `INFO`: Normal operation messages (start, completion)
- `WARNING`: Warnings and HMS stderr output
- `SEVERE`: Errors and failures

HMS stdout messages are logged at DEBUG level and not shown by default to reduce noise.

## Headless Operation

The container is configured for fully headless operation with no display or GUI dependencies:
- Java AWT headless mode enabled
- Software rendering (no GPU required)
- Monocle headless platform for JavaFX
- Suitable for CI/CD pipelines, cloud functions, and serverless environments