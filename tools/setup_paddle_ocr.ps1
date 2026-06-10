$ErrorActionPreference = "Stop"

python -m pip install uv
python -m uv python install 3.12
python -m uv venv --python 3.12 .paddle-ocr-venv

$paddlePython = Join-Path $PSScriptRoot "..\.paddle-ocr-venv\Scripts\python.exe"
& $paddlePython -m ensurepip --upgrade
& $paddlePython -m pip install paddlepaddle==3.2.2 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
& $paddlePython -m pip install paddleocr==3.6.0 "numpy>=1.24,<2.4"

Write-Output "PaddleOCR environment is ready: $paddlePython"
