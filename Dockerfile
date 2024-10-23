# Use the specified platform and base image
FROM --platform=amd64 condaforge/miniforge3@sha256:5367f97080d9cebdead119133af9293aed736fd0499b93cd940c8b97240b7b19

# Install all necessary apt packages in a single RUN command to reduce layers
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        netcat \
        nano \
        git \
        make \
        gcc \
        wget \
        g++ \
        libxrender1 \
        libxext6 && \
    apt-get autoremove -y && \
    apt-get clean -y && \
    rm -rf /var/lib/apt/lists/*

# Set Python environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Set the working directory
WORKDIR /code

# Copy environment.yml first to leverage Docker cache if dependencies haven't changed
COPY environment.yml .

# Clone the required Git repositories with shallow clones for efficiency
RUN git clone --single-branch --branch main --depth 1 https://github.com/ReactionMechanismGenerator/RMG-Py.git /code/RMG-Py && \
    git clone --single-branch --branch main --depth 1 https://github.com/ReactionMechanismGenerator/RMG-database.git /code/RMG-database

# Set PATH and PYTHONPATH in a single ENV command to minimize layers
ENV PATH=/opt/conda/envs/tck_env/bin:/code/RMG-Py:/code/RMG-database:$PATH \
    PYTHONPATH=/code/tckdb:/code/RMG-Py:/code/RMG-database:/code

# Create the Conda environment and clean up to reduce image size
RUN mamba env create -f environment.yml && \
    mamba clean --all -y

# Use the new Conda environment for subsequent RUN commands
SHELL ["conda", "run", "--no-capture-output", "-n", "tck_env", "/bin/bash", "-c"]

# Build RMG-Py within the Conda environment
WORKDIR /code/RMG-Py
RUN make

# Return to the main working directory and copy application code
WORKDIR /code

# (Optional) Expose necessary ports
EXPOSE 8000

ENTRYPOINT ["python",  "/code/tckdb/backend/app/main.py"]
