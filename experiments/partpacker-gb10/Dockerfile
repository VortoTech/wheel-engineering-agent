FROM nvcr.io/nvidia/vllm:26.02-py3

ARG PARTPACKER_COMMIT=178d6c9408ecde857e6e716bab2763df6ce8bdc0
LABEL org.wheelcam.experiment="partpacker-gb10" \
      org.wheelcam.partpacker.commit="${PARTPACKER_COMMIT}"

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      libegl1 libgl1 libglib2.0-0 libgomp1 libjpeg-turbo8 libpng16-16t64 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-arm64.txt /opt/wheelcam/requirements-arm64.txt
COPY wheels/Cython-3.0.12-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.whl /opt/wheelcam/Cython-3.0.12-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.whl
COPY wheels/scipy-1.18.1-cp312-cp312-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl /opt/wheelcam/scipy-1.18.1-cp312-cp312-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl
RUN echo "a7fec4f052b8fe173fe70eae75091389955b9a23d5cec3d576d21c5913b49d47  /opt/wheelcam/Cython-3.0.12-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.whl" | sha256sum -c - \
    && echo "3c085faa2cfa879c5141df483f836f4d691045a078224a670fa570fa01612d89  /opt/wheelcam/scipy-1.18.1-cp312-cp312-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl" | sha256sum -c - \
    && PIP_CONSTRAINT=/dev/null python3 -m pip install --no-cache-dir /opt/wheelcam/Cython-3.0.12-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.whl \
    && PIP_CONSTRAINT=/dev/null python3 -m pip install --no-cache-dir /opt/wheelcam/scipy-1.18.1-cp312-cp312-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl \
    && python3 -m pip install --no-cache-dir --no-deps --no-build-isolation pymcubes==0.1.6 \
    && PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10 python3 -m pip install --no-cache-dir -r /opt/wheelcam/requirements-arm64.txt

COPY wheels/kiui-0.2.16-py3-none-any.whl /opt/wheelcam/kiui-0.2.16-py3-none-any.whl
RUN echo "a33453b76a689cbc8be3fcf8f0cb6e16b4c9b3c78956e5e0bf6270077ccfa2cf  /opt/wheelcam/kiui-0.2.16-py3-none-any.whl" | sha256sum -c - \
    && PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10 python3 -m pip install --no-cache-dir /opt/wheelcam/kiui-0.2.16-py3-none-any.whl

COPY wheelcam_mesh_compat.py apply_compat.py validate_compat.py run_partpacker.sh /opt/wheelcam/
RUN chmod 0755 /opt/wheelcam/run_partpacker.sh \
    && python3 /opt/wheelcam/validate_compat.py

WORKDIR /work
ENTRYPOINT ["/opt/wheelcam/run_partpacker.sh"]
