"""Serve an open-source coding model on a GPU with vLLM, exposed as an OpenAI-compatible API.

Deploy:   modal deploy gpu/vllm_modal.py
Use:      model = "openai_compat:https://<workspace>--forge-vllm-serve.modal.run/v1|qwen3-coder"

vLLM was chosen for its throughput (continuous batching) and built-in tool-call parsers;
Modal for per-second GPU billing with no cluster to manage.
"""

import subprocess

import modal

MODEL = "Qwen/Qwen3-Coder-30B-A3B-Instruct"
SERVED_NAME = "qwen3-coder"
PORT = 8000

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("vllm==0.10.2", "huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)
weights = modal.Volume.from_name("forge-hf-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("forge-vllm-cache", create_if_missing=True)

app = modal.App("forge-vllm")


@app.function(
    image=image,
    gpu="H100",
    volumes={"/root/.cache/huggingface": weights, "/root/.cache/vllm": vllm_cache},
    timeout=60 * 60,
    scaledown_window=15 * 60,  # stay warm between bursts of agent turns
    min_containers=0,          # set to 1 while actively using it to avoid cold starts
)
@modal.concurrent(max_inputs=32)
@modal.web_server(port=PORT, startup_timeout=20 * 60)
def serve() -> None:
    subprocess.Popen(
        [
            "vllm", "serve", MODEL,
            "--served-model-name", SERVED_NAME,
            "--host", "0.0.0.0", "--port", str(PORT),
            "--max-model-len", "65536",
            "--enable-auto-tool-choice",
            "--tool-call-parser", "qwen3_coder",
        ]
    )
