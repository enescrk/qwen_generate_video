import runpod
import os
import websocket
import base64
import json
import uuid
import logging
import urllib.request
import urllib.parse
import binascii
import subprocess
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ✅ sanitize: "http://127.0.0.1:8188/" gibi gelse bile host'u temizle
server_address = os.getenv("SERVER_ADDRESS", "127.0.0.1").strip()
server_address = server_address.replace("http://", "").replace("https://", "")
server_address = server_address.split("/")[0]
server_address = server_address.split(":")[0]  # port gelirse at

client_id = str(uuid.uuid4())


def to_nearest_multiple_of_16(value):
    """주어진 값을 가장 가까운 16의 배수로 보정, 최소 16 보장"""
    try:
        numeric_value = float(value)
    except Exception:
        raise Exception("width/height 값이 숫자가 아닙니다: {}".format(value))
    adjusted = int(round(numeric_value / 16.0) * 16)
    if adjusted < 16:
        adjusted = 16
    return adjusted


def process_input(input_data, temp_dir, output_filename, input_type):
    """입력 데이터를 처리하여 파일 경로를 반환하는 함수"""
    if input_type == "path":
        logger.info("📁 경로 입력 처리: {}".format(input_data))
        return input_data
    elif input_type == "url":
        logger.info("🌐 URL 입력 처리: {}".format(input_data))
        os.makedirs(temp_dir, exist_ok=True)
        file_path = os.path.abspath(os.path.join(temp_dir, output_filename))
        return download_file_from_url(input_data, file_path)
    elif input_type == "base64":
        logger.info("🔢 Base64 입력 처리")
        return save_base64_to_file(input_data, temp_dir, output_filename)
    else:
        raise Exception("지원하지 않는 입력 타입: {}".format(input_type))


def download_file_from_url(url, output_path):
    """URL에서 파일을 다운로드하는 함수"""
    try:
        result = subprocess.run(
            [
                "curl",
                "-L",
                "--fail",
                "--silent",
                "--show-error",
                "--retry",
                "3",
                "--retry-delay",
                "1",
                "-o",
                output_path,
                url,
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            logger.info("✅ URL 다운로드 성공: {} -> {}".format(url, output_path))
            return output_path
        else:
            logger.error("❌ curl 다운로드 실패: {}".format(result.stderr))
            raise Exception("URL 다운로드 실패: {}".format(result.stderr))
    except subprocess.TimeoutExpired:
        logger.error("❌ 다운로드 시간 초과")
        raise Exception("다운로드 시간 초과")
    except Exception as e:
        logger.error("❌ 다운로드 중 오류 발생: {}".format(e))
        raise Exception("다운로드 중 오류 발생: {}".format(e))


def save_base64_to_file(base64_data, temp_dir, output_filename):
    """Base64 데이터를 파일로 저장하는 함수"""
    try:
        decoded_data = base64.b64decode(base64_data)
        os.makedirs(temp_dir, exist_ok=True)

        file_path = os.path.abspath(os.path.join(temp_dir, output_filename))
        with open(file_path, "wb") as f:
            f.write(decoded_data)

        logger.info("✅ Base64 입력을 '{}' 파일로 저장했습니다.".format(file_path))
        return file_path
    except (binascii.Error, ValueError) as e:
        logger.error("❌ Base64 디코딩 실패: {}".format(e))
        raise Exception("Base64 디코딩 실패: {}".format(e))


def queue_prompt(prompt):
    url = "http://{}:8188/prompt".format(server_address)
    logger.info("Queueing prompt to: {}".format(url))
    p = {"prompt": prompt, "client_id": client_id}
    data = json.dumps(p).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    return json.loads(urllib.request.urlopen(req).read())


def get_history(prompt_id):
    url = "http://{}:8188/history/{}".format(server_address, prompt_id)
    logger.info("Getting history from: {}".format(url))
    with urllib.request.urlopen(url) as response:
        return json.loads(response.read())


def get_videos(ws, prompt):
    prompt_id = queue_prompt(prompt)["prompt_id"]
    output_videos = {}

    while True:
        out = ws.recv()
        if isinstance(out, str):
            message = json.loads(out)
            if message.get("type") == "executing":
                data = message.get("data", {})
                if data.get("node") is None and data.get("prompt_id") == prompt_id:
                    break
        else:
            continue

    history = get_history(prompt_id)[prompt_id]
    for node_id in history["outputs"]:
        node_output = history["outputs"][node_id]
        videos_output = []
        if "gifs" in node_output:
            for video in node_output["gifs"]:
                with open(video["fullpath"], "rb") as f:
                    video_data = base64.b64encode(f.read()).decode("utf-8")
                videos_output.append(video_data)
        output_videos[node_id] = videos_output

    return output_videos


def load_workflow(workflow_path):
    with open(workflow_path, "r") as file:
        return json.load(file)


def handler(job):
    job_input = job.get("input", {})
    logger.info("Received job input: {}".format(job_input))

    task_id = "task_{}".format(uuid.uuid4())

    # --- Input image ---
    if "image_path" in job_input:
        image_path = process_input(job_input["image_path"], task_id, "input_image.jpg", "path")
    elif "image_url" in job_input:
        image_path = process_input(job_input["image_url"], task_id, "input_image.jpg", "url")
    elif "image_base64" in job_input:
        image_path = process_input(job_input["image_base64"], task_id, "input_image.jpg", "base64")
    else:
        image_path = "/example_image.png"
        logger.info("기본 이미지 파일을 사용합니다: /example_image.png")

    # --- Optional end image (FLF2V) ---
    end_image_path_local = None
    if "end_image_path" in job_input:
        end_image_path_local = process_input(job_input["end_image_path"], task_id, "end_image.jpg", "path")
    elif "end_image_url" in job_input:
        end_image_path_local = process_input(job_input["end_image_url"], task_id, "end_image.jpg", "url")
    elif "end_image_base64" in job_input:
        end_image_path_local = process_input(job_input["end_image_base64"], task_id, "end_image.jpg", "base64")

    # --- LoRA pairs ---
    lora_pairs = job_input.get("lora_pairs", [])
    lora_count = min(len(lora_pairs), 4)
    if len(lora_pairs) > 4:
        logger.warning("LoRA 개수가 {}개입니다. 최대 4개까지만 지원됩니다. 처음 4개만 사용합니다.".format(len(lora_pairs)))
        lora_pairs = lora_pairs[:4]

    # --- Select workflow ---
    workflow_file = "/new_Wan22_flf2v_api.json" if end_image_path_local else "/new_Wan22_api.json"
    logger.info("Using {} workflow with {} LoRA pairs".format("FLF2V" if end_image_path_local else "single", lora_count))

    prompt = load_workflow(workflow_file)

    # --- Read params ---
    length = int(job_input.get("length", 81))
    steps = int(job_input.get("steps", 8))
    cfg = float(job_input.get("cfg", 2.0))
    fps = int(job_input.get("fps", 16))
    seed = int(job_input.get("seed", 42))

    original_width = job_input.get("width", 480)
    original_height = job_input.get("height", 832)

    # --- Disable CPU offload ---
    for nid in ("135", "220", "540", "541"):
        if nid in prompt and "inputs" in prompt[nid]:
            prompt[nid]["inputs"]["force_offload"] = False

    # --- Apply main inputs ---
    prompt["244"]["inputs"]["image"] = image_path
    prompt["541"]["inputs"]["num_frames"] = length
    prompt["135"]["inputs"]["positive_prompt"] = job_input.get("prompt", "")
    prompt["135"]["inputs"]["negative_prompt"] = job_input.get(
        "negative_prompt",
        "bright tones, overexposed, static, blurred details, subtitles, style, works, paintings, images, static, overall gray, worst quality, low quality, JPEG compression residue, ugly, incomplete, extra fingers, poorly drawn hands, poorly drawn faces, deformed, disfigured, misshapen limbs, fused fingers, still picture, messy background, three legs, many people in the background, walking backwards",
    )
    prompt["220"]["inputs"]["seed"] = seed
    prompt["540"]["inputs"]["seed"] = seed

    adjusted_width = to_nearest_multiple_of_16(original_width)
    adjusted_height = to_nearest_multiple_of_16(original_height)
    if adjusted_width != original_width:
        logger.info("Width adjusted: {} -> {}".format(original_width, adjusted_width))
    if adjusted_height != original_height:
        logger.info("Height adjusted: {} -> {}".format(original_height, adjusted_height))
    prompt["235"]["inputs"]["value"] = adjusted_width
    prompt["236"]["inputs"]["value"] = adjusted_height

    if "498" in prompt:
        prompt["498"]["inputs"]["context_overlap"] = int(job_input.get("context_overlap", 48))
        prompt["498"]["inputs"]["context_frames"] = length

    if "569" in prompt:
        prompt["569"]["inputs"]["value"] = steps

    if "575" in prompt:
        lowsteps = int(round(steps * 0.6))
        lowsteps = max(1, min(lowsteps, steps - 1))
        prompt["575"]["inputs"]["value"] = lowsteps
    else:
        lowsteps = None

    if "570" in prompt:
        prompt["570"]["inputs"]["cfg_scale_start"] = cfg
        prompt["570"]["inputs"]["cfg_scale_end"] = cfg

    if "540" in prompt:
        prompt["540"]["inputs"]["cfg"] = cfg

    if "131" in prompt:
        prompt["131"]["inputs"]["frame_rate"] = fps

    logger.info("✅ Applied: length={}, steps={}, split={}, cfg={}, fps={}, seed={}".format(length, steps, lowsteps, cfg, fps, seed))

    if end_image_path_local:
        prompt["617"]["inputs"]["image"] = end_image_path_local

    if lora_count > 0:
        high_lora_node_id = "279"
        low_lora_node_id = "553"

        for i, lora_pair in enumerate(lora_pairs[:4]):
            lora_high = lora_pair.get("high")
            lora_low = lora_pair.get("low")
            lora_high_weight = float(lora_pair.get("high_weight", 1.0))
            lora_low_weight = float(lora_pair.get("low_weight", 1.0))

            if lora_high:
                prompt[high_lora_node_id]["inputs"]["lora_{}".format(i + 1)] = lora_high
                prompt[high_lora_node_id]["inputs"]["strength_{}".format(i + 1)] = lora_high_weight
                logger.info("LoRA {} HIGH applied: {} w={}".format(i + 1, lora_high, lora_high_weight))

            if lora_low:
                prompt[low_lora_node_id]["inputs"]["lora_{}".format(i + 1)] = lora_low
                prompt[low_lora_node_id]["inputs"]["strength_{}".format(i + 1)] = lora_low_weight
                logger.info("LoRA {} LOW applied: {} w={}".format(i + 1, lora_low, lora_low_weight))

    ws_url = "ws://{}:8188/ws?clientId={}".format(server_address, client_id)
    logger.info("Connecting to WebSocket: {}".format(ws_url))

    http_url = "http://{}:8188/".format(server_address)
    logger.info("DEBUG http_url={} server_address={!r}".format(http_url, server_address))

    max_http_attempts = 180
    for http_attempt in range(max_http_attempts):
        try:
            urllib.request.urlopen(http_url, timeout=5)
            logger.info("HTTP 연결 성공 (시도 {})".format(http_attempt + 1))
            break
        except Exception as e:
            logger.warning("HTTP 연결 실패 (시도 {}/{}): {}".format(http_attempt + 1, max_http_attempts, e))
            if http_attempt == max_http_attempts - 1:
                raise Exception("ComfyUI 서버에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.")
            time.sleep(1)

    ws = websocket.WebSocket()
    max_attempts = int(180 / 5)
    for attempt in range(max_attempts):
        try:
            ws.connect(ws_url)
            logger.info("웹소켓 연결 성공 (시도 {})".format(attempt + 1))
            break
        except Exception as e:
            logger.warning("웹소켓 연결 실패 (시도 {}/{}): {}".format(attempt + 1, max_attempts, e))
            if attempt == max_attempts - 1:
                raise Exception("웹소켓 연결 시간 초과 (3분)")
            time.sleep(5)

    videos = get_videos(ws, prompt)
    ws.close()

    for node_id in videos:
        if videos[node_id]:
            return {"video": videos[node_id][0]}

    return {"error": "비디오를 찾을 수 없습니다."}


runpod.serverless.start({"handler": handler})
