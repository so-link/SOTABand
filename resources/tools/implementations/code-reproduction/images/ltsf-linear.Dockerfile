ARG BASE_IMAGE=sotaband-academic-gpu:1.0
FROM ${BASE_IMAGE}

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 180 --retries 10 \
    numpy==1.26.4 \
    pandas==2.2.3 \
    scikit-learn==1.5.2 \
    matplotlib==3.9.2
