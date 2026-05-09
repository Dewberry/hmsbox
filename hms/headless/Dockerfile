# Build arguments
ARG HMS_VERSION=4.14-beta.1

# First stage: Build the jar for calling hms compute
FROM eclipse-temurin:17-jdk-jammy AS javabuilder

ARG HMS_VERSION

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    unzip \
    libxrender1 \
    libxtst6 \
    libxi6 \
    libfreetype6 \
    libgfortran5 \
    libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir /app
WORKDIR /app

# Java and Gradle setup
RUN wget https://services.gradle.org/distributions/gradle-7.3.1-bin.zip && \
    mkdir /opt/gradle && \
    unzip -d /opt/gradle gradle-7.3.1-bin.zip && \
    rm gradle-7.3.1-bin.zip
ENV PATH=$PATH:/opt/gradle/gradle-7.3.1/bin

# Download and extract HMS once (large layer, cached separately)
# Use tar --exclude to skip unnecessary files during extraction
# Note: HMS 4.13 has a malformed filename with duplicate -linux64 suffix
RUN set -e && \
    mkdir -p /tmp/hms-extract && \
    FILE1="hec-hms-${HMS_VERSION}-linux64.tar.gz" && \
    FILE2="hec-hms-${HMS_VERSION}-linux64-linux64.tar.gz" && \
    URL1="https://www.hec.usace.army.mil/nexus/repository/maven-public/mil/army/usace/hec/hec-hms/${HMS_VERSION}-linux64/${FILE1}" && \
    URL2="https://www.hec.usace.army.mil/nexus/repository/maven-public/mil/army/usace/hec/hec-hms/${HMS_VERSION}-linux64/${FILE2}" && \
    (wget "${URL1}" -P /tmp 2>/dev/null) || wget "${URL2}" -P /tmp && \
    if [ -f /tmp/${FILE1} ]; then TARFILE=${FILE1}; else TARFILE=${FILE2}; fi && \
    tar -xzf /tmp/${TARFILE} -C /tmp/hms-extract \
      --exclude='*.zip' \
      --exclude='*.exe' \
      --exclude='*.bat' \
      --exclude='*.cmd' \
      --exclude='*/samples' \
      --exclude='*/docs' \
      --exclude='*/examples' \
      --exclude='*/vortex' && \
    mkdir -p /hec-hms && \
    echo "Contents of /tmp/hms-extract:" && ls -la /tmp/hms-extract && \
    # Find the main HMS directory (could be HEC-HMS-* or hec-hms-*)
    HMS_DIR=$(find /tmp/hms-extract -maxdepth 1 -type d -name "*HEC-HMS*" -o -name "*hec-hms*" | head -1) && \
    if [ -z "$HMS_DIR" ]; then \
      echo "ERROR: Could not find HMS directory in extracted files"; \
      ls -la /tmp/hms-extract; \
      exit 1; \
    fi && \
    echo "Found HMS directory: $HMS_DIR" && \
    mv "$HMS_DIR" /hec-hms/HEC-HMS-${HMS_VERSION} && \
    rm -rf /tmp/hms-extract /tmp/${TARFILE}

# Copy and build source (separate layer allows better caching of HMS deps)
COPY ./build.gradle ./gradlew /app/
RUN gradle --version

RUN mkdir -p /app/src/main/java/dewberry
COPY ./RunHMS.java /app/src/main/java/dewberry/RunHMS.java
RUN gradle build --no-daemon -PhmsVersion=${HMS_VERSION} && chmod +x /app/build/libs/hms-compute.jar


# Final stage: create the production container
FROM debian:bookworm-slim AS prod

ARG HMS_VERSION=4.14-beta.1
ENV HMS_VERSION=${HMS_VERSION}

LABEL org.opencontainers.image.title="HEC-HMS Headless Container" \
      org.opencontainers.image.description="Docker container for running HEC-HMS hydrological simulations in headless mode" \
      org.opencontainers.image.version="${HMS_VERSION}" \
      org.opencontainers.image.vendor="Dewberry" \
      org.opencontainers.image.source="https://github.com/Dewberry/hmsbox" \
      org.opencontainers.image.documentation="https://github.com/Dewberry/hmsbox/tree/main/hms/headless"

RUN apt-get update && apt-get install -y --no-install-recommends \
    libxrender1 libxtst6 libxi6 libfreetype6 libgfortran5 libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 hms

COPY --from=javabuilder /hec-hms /hec-hms
RUN rm -f /hec-hms/lib && mkdir -p /hec-hms/lib
COPY --from=javabuilder /app/build/libs/hms-compute.jar /hec-hms/lib/hms-compute.jar

# Verify HMS directory structure
RUN echo "=== HMS Directory Structure ===" && \
    ls -la /hec-hms/ && \
    echo "=== HEC-HMS subdirectories ===" && \
    find /hec-hms -maxdepth 2 -type d | head -20 && \
    echo "=== Checking for hms.jar ===" && \
    find /hec-hms -name "hms.jar" && \
    echo "=== Checking for lib directory ===" && \
    ls -la /hec-hms/HEC-HMS-* 2>/dev/null | head -20 || echo "HEC-HMS directory not found with expected naming" && \
    echo "=== Searching for GDAL native libraries ===" && \
    find /hec-hms -name "*gdal*jni*" -o -name "libgdal*" | head -20 && \
    echo "=== Checking bin directories ===" && \
    find /hec-hms -type d -name "bin" -exec ls -la {} \;

# Create logging configuration files to suppress verbose output
RUN mkdir -p /etc/hms-logging && \
    cat > /etc/hms-logging/logging.properties <<'EOF'
handlers= java.util.logging.ConsoleHandler
.level= INFO
java.util.logging.ConsoleHandler.level = INFO
java.util.logging.ConsoleHandler.formatter = java.util.logging.SimpleFormatter
java.util.logging.SimpleFormatter.format=%1$tY-%1$tm-%1$td %1$tH:%1$tM:%1$tS %4$s: %5$s%n
EOF

RUN cat > /etc/hms-logging/logback.xml <<'EOF'
<configuration>
  <root level="ERROR"/>
</configuration>
EOF

RUN cat > /etc/hms-logging/log4j2.xml <<'EOF'
<Configuration status="WARN">
  <Loggers>
    <Root level="error"/>
  </Loggers>
</Configuration>
EOF

ENV HMS_HOME=/hec-hms/HEC-HMS-${HMS_VERSION}
ENV JAVA_EXE=$HMS_HOME/jre/bin/java
ENV JAVA_HOME=$HMS_HOME/jre
ENV PROG=hms.hms
ENV PATH=$HMS_HOME/bin/taudem:$HMS_HOME/bin/mpi:$HMS_HOME/bin:$HMS_HOME/jre/bin/:$HMS_HOME/bin/:$HMS_HOME/jre/lib/:$PATH
ENV GDAL_DATA=$HMS_HOME/bin/gdal/gdal-data
ENV PROJ_LIB=$HMS_HOME/bin/gdal/proj
ENV CLASSPATH=$HMS_HOME/*:$HMS_HOME/lib/*:$HMS_HOME/lib/hec/*:$HMS_HOME/jre/lib/*
ENV HDF5_DISABLE_ERROR_STACK=1

RUN chmod +x $HMS_HOME/jre/bin/java && chown -R hms:hms /hec-hms

# Create a shell wrapper for proper variable expansion at build time (before USER hms)
RUN cat > /usr/local/bin/run-hms.sh <<'SCRIPT'
#!/bin/bash
set -e
HMS_VERSION=${HMS_VERSION:-4.12}
HMS_HOME=/hec-hms/HEC-HMS-${HMS_VERSION}
JAVA_EXE=${HMS_HOME}/jre/bin/java

if [ ! -f "$JAVA_EXE" ]; then
  echo "ERROR: Java executable not found at $JAVA_EXE"
  ls -la ${HMS_HOME}/jre/bin/ 2>/dev/null || echo "JRE bin directory not found"
  exit 1
fi

# Build library path by finding all potential library locations
LIBRARY_PATH="${HMS_HOME}/bin:${HMS_HOME}/lib:${HMS_HOME}/jre/lib/server"

# Add any gdal-related directories
if [ -d "${HMS_HOME}/bin/gdal" ]; then
  LIBRARY_PATH="${LIBRARY_PATH}:${HMS_HOME}/bin/gdal"
fi

# Search for native libraries recursively
for libdir in $(find ${HMS_HOME} -type d -name "lib*" 2>/dev/null); do
  LIBRARY_PATH="${LIBRARY_PATH}:${libdir}"
done

# Add /hec-hms bin directories
LIBRARY_PATH="${LIBRARY_PATH}:/hec-hms/bin"
if [ -d "/hec-hms/bin/gdal" ]; then
  LIBRARY_PATH="${LIBRARY_PATH}:/hec-hms/bin/gdal"
fi

exec $JAVA_EXE \
  -XX:+ExitOnOutOfMemoryError \
  -XX:MaxRAMPercentage=75 \
  -XX:+UseContainerSupport \
  -Djava.library.path=${LIBRARY_PATH} \
  -Djava.util.logging.config.file=/etc/hms-logging/logging.properties \
  -Dorg.slf4j.simpleLogger.defaultLogLevel=error \
  -Dorg.slf4j.simpleLogger.showDateTime=false \
  -Dorg.slf4j.simpleLogger.showThreadName=false \
  -Dlogback.configurationFile=/etc/hms-logging/logback.xml \
  -Dlog4j.configurationFile=/etc/hms-logging/log4j2.xml \
  -Dlog4j2.configurationFile=/etc/hms-logging/log4j2.xml \
  -Dlog4j2.statusLoggerLevel=OFF \
  -Djava.awt.headless=true \
  -Dprism.order=sw \
  -Dprism.text=t2k \
  -Dprism.verbose=false \
  -Dglass.platform=Monocle \
  -Dmonocle.platform=Headless \
  -Dtestfx.robot=glass \
  -Dtestfx.headless=true \
  -cp "/hec-hms/lib/*:${HMS_HOME}/*:${HMS_HOME}/lib/*:${HMS_HOME}/lib/hec/*" \
  dewberry.RunHMS "$@"
SCRIPT

RUN chmod +x /usr/local/bin/run-hms.sh

WORKDIR /app
RUN chown hms:hms /app
USER hms

ENTRYPOINT ["/usr/local/bin/run-hms.sh"]