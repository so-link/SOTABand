ARG BASE_IMAGE=python:3.9-slim
FROM ${BASE_IMAGE}

# The upstream DGI implementation imports SciPy's pre-1.8 ARPACK module.
RUN python -c "import sys; assert sys.version_info[:2] == (3, 9), 'DGI image requires Python 3.9'"
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 60 --retries 2 torch==2.5.1 --index-url https://download.pytorch.org/whl/cu124
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 60 --retries 2 numpy==1.21.6 scipy==1.7.3 networkx==2.6.3
RUN python -c "import torch, numpy, networkx; from scipy.sparse.linalg.eigen.arpack import eigsh"
WORKDIR /workspace
