# Dockerfile for DELTA pipeline (including physiological regressor generation)
# Replaces MATLAB + TAPAS PhysIO with pure Python (NumPy / SciPy / NiBabel).
#
# Build:
#   docker build -t delta:latest .
#
# Run physio step:
#   docker run --rm \
#     -v /path/to/bids:/data/bids \
#     -v /path/to/state:/data/state \
#     delta:latest physio \
#       --bids /data/bids \
#       --tasks sentencecompletion \
#       --state /data/state/deltastate.json

FROM python:3.11-slim

LABEL org.opencontainers.image.title="DELTA neuroimaging pipeline" \
      org.opencontainers.image.description="DICOM→BIDS→fMRIPrep→Physio pipeline" \
      org.opencontainers.image.licenses="MIT"

# System deps: dcm2bids needs dcm2niix; bids-validator needs Node (optional)
RUN apt-get update && apt-get install -y --no-install-recommends \
        dcm2niix \
        curl \
        git \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install dcm2bids + bids-validator (deno-based, no Node needed)
RUN pip install --no-cache-dir \
        dcm2bids==3.2.0 \
        nibabel>=5.0 \
        numpy>=1.26 \
        scipy>=1.12

# Install bids-validator (standalone binary via deno)
RUN curl -fsSL https://deno.land/install.sh | sh && \
    /root/.deno/bin/deno install --allow-all --no-check \
        -n bids-validator-deno \
        npm:bids-validator@1.14.6 2>/dev/null || true
ENV PATH="/root/.deno/bin:${PATH}"

# Copy pipeline source
COPY src/ /opt/delta/src/
COPY configs/ /opt/delta/configs/

WORKDIR /opt/delta

ENTRYPOINT ["python3", "src/cli.py"]
