"""Start a private llama-server for the recording and keep it up until a stop file appears. Refuses if the GPU is busy (the repo's own guard)."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, r"C:\PROFESSIONAL\Development\AI-HPC\sf-wt-fmt")
os.environ["SILICONFORGE_HOME"] = r"C:\PROFESSIONAL\Development\AI-HPC\siliconforge"
from services.orchestrator.inference.llamacpp import LlamaCpp, LlamaServer, ServerConfig

stop = Path(sys.argv[1])
srv = LlamaServer(LlamaCpp().server_exe, ServerConfig(model_path=Path(r"C:\PROFESSIONAL\Development\AI-HPC\siliconforge\models\gguf\Qwen3.5-9B-Q4_K_M.gguf"),
                  ctx=16384, kv_type="q8_0", port=8189, reasoning="auto", spec_type="none"))
srv.start()
print("llm ready", flush=True)
while not stop.exists():
    time.sleep(1)
srv.stop()
print("llm stopped", flush=True)
