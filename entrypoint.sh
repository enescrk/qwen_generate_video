#!/bin/bash
set -e

echo "Starting ComfyUI in the background..."
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
python /ComfyUI/main.py --listen --use-sage-attention --disable-metadata &
COMFY_PID=$!
trap "echo 'Stopping ComfyUI...'; kill $COMFY_PID 2>/dev/null || true" EXIT

echo "Waiting for ComfyUI to be ready..."
max_wait=120
wait_count=0
while [ $wait_count -lt $max_wait ]; do
  if curl -s http://127.0.0.1:8188/ > /dev/null 2>&1; then
    echo "ComfyUI is ready!"
    break
  fi
  sleep 2
  wait_count=$((wait_count + 2))
done

if [ $wait_count -ge $max_wait ]; then
  echo "Error: ComfyUI failed to start within $max_wait seconds"
  exit 1
fi

echo "Starting the handler..."
exec python handler.py