import base64
import json
import logging
import os

import azure.cognitiveservices.speech as speechsdk
import azure.functions as func
import requests
from dotenv import load_dotenv


load_dotenv(override=True)

app = func.FunctionApp()


@app.route(route="translate", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def translate(req: func.HttpRequest) -> func.HttpResponse:
    """
    HTTP Trigger: Receives audio + translation config, returns translated audio
    Pipeline: STT (Azure Speech) -> Translate (Azure Translator) -> TTS (Azure Speech)
    """

    logging.info("Translation request received")
    current_stage = "parse_request"

    try:
        req_body = req.get_json()
    except ValueError:
        logging.exception("Invalid JSON payload")
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON payload"}),
            status_code=400,
            mimetype="application/json",
        )

    source_locale = req_body.get("source_locale")
    target_locale = req_body.get("target_locale")
    neural_voice = req_body.get("neural_voice")
    audio_data_b64 = req_body.get("audio_data")

    if not all([source_locale, target_locale, neural_voice, audio_data_b64]):
        logging.warning("Missing required fields in request body")
        return func.HttpResponse(
            json.dumps({"error": "Missing required fields"}),
            status_code=400,
            mimetype="application/json",
        )

    logging.info(
        "Processing request: %s -> %s, voice: %s",
        source_locale,
        target_locale,
        neural_voice,
    )

    current_stage = "decode_audio"
    try:
        audio_bytes = base64.b64decode(audio_data_b64)
    except (ValueError, TypeError) as exc:
        logging.exception("Failed to decode audio data at stage %s", current_stage)
        return func.HttpResponse(
            json.dumps({"error": f"Invalid audio data: {exc}", "stage": current_stage}),
            status_code=400,
            mimetype="application/json",
        )

    logging.info("Stage %s complete: %d bytes decoded", current_stage, len(audio_bytes))

    try:
        current_stage = "speech_to_text"
        transcribed_text = speech_to_text(audio_bytes, source_locale)
        logging.info("Stage %s output: %s", current_stage, transcribed_text)

        current_stage = "translate_text"
        translated_text = translate_text(
            transcribed_text, source_locale, target_locale
        )
        logging.info("Stage %s output: %s", current_stage, translated_text)

        current_stage = "text_to_speech"
        translated_audio = text_to_speech(translated_text, target_locale, neural_voice)
        logging.info("Stage %s complete: %d bytes generated", current_stage, len(translated_audio))
    except Exception as exc:  # pylint: disable=broad-except
        logging.exception("Processing pipeline failed at stage %s", current_stage)
        return func.HttpResponse(
            json.dumps({"error": str(exc), "stage": current_stage}),
            status_code=500,
            mimetype="application/json",
        )

    return func.HttpResponse(
        body=translated_audio,
        status_code=200,
        mimetype="audio/wav",
    )


def speech_to_text(audio_bytes: bytes, locale: str) -> str:
    """Convert audio to text using Azure Speech Service using the provided locale."""

    speech_key = os.getenv("AZURE_SPEECH_KEY") or os.getenv("AZURE_SPEECH_API_KEY")
    speech_region = os.getenv("AZURE_SPEECH_REGION") or os.getenv("AZURE_SPEECH_LOCATION")
    if not speech_key or not speech_region:
        raise RuntimeError("Azure Speech credentials are not configured")

    speech_config = speechsdk.SpeechConfig(
        subscription=speech_key,
        region=speech_region,
    )
    speech_config.speech_recognition_language = locale
    logging.info(
        "Stage %s config: region=%s locale=%s",
        "speech_to_text",
        speech_region,
        locale,
    )

    # NEW: Define audio format explicitly (16kHz, 16-bit, mono PCM)
    audio_format = speechsdk.audio.AudioStreamFormat(
        samples_per_second=16000,
        bits_per_sample=16,
        channels=1
    )

    # NEW: Create push stream with format specification
    audio_stream = speechsdk.audio.PushAudioInputStream(stream_format=audio_format)

    # NEW: Strip WAV header (first 44 bytes) if present
    if len(audio_bytes) > 44 and audio_bytes[:4] == b"RIFF":
        logging.info("WAV header detected, stripping 44 bytes")
        audio_bytes = audio_bytes[44:]

    audio_stream.write(audio_bytes)
    audio_stream.close()

    audio_config = speechsdk.audio.AudioConfig(stream=audio_stream)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )

    result = recognizer.recognize_once()
    if result.reason == speechsdk.ResultReason.RecognizedSpeech:
        return result.text
    if result.reason == speechsdk.ResultReason.NoMatch:
        raise RuntimeError("STT failed: No speech could be recognized")
    if result.reason == speechsdk.ResultReason.Canceled:
        cancellation = result.cancellation_details
        raise RuntimeError(f"STT canceled: {cancellation.reason}, {cancellation.error_details}")
    raise RuntimeError(f"STT failed: {result.reason}")


def translate_text(text: str, source_locale: str, target_locale: str) -> str:
    """Translate text using Azure Translator based on locale-derived language codes."""

    def _extract_language_code(locale: str) -> str:
        return locale.split("-", 1)[0] if locale else ""

    from_lang = _extract_language_code(source_locale)
    to_lang = _extract_language_code(target_locale)
    if not from_lang or not to_lang:
        raise RuntimeError("Invalid locale provided for translation")

    raw_endpoint = (
        os.getenv("AZURE_TRANSLATE_ENDPOINT")
        or os.getenv("AZURE_TRANSLATOR_ENDPOINT")
        or os.getenv("AZURE_TRANSLATOR_URL")
    )
    endpoint = raw_endpoint.rstrip("/") if raw_endpoint else None
    key = os.getenv("AZURE_TRANSLATE_KEY") or os.getenv("AZURE_TRANSLATOR_KEY")
    region_value = (
        os.getenv("AZURE_TRANSLATE_REGION")
        or os.getenv("AZURE_TRANSLATOR_REGION")
        or os.getenv("AZURE_TRANSLATOR_LOCATION")
        or ""
    ).strip()
    if not endpoint or not key:
        raise RuntimeError("Azure Translator credentials are not configured")

    path = f"/translate?api-version=3.0&from={from_lang}&to={to_lang}"
    url = endpoint + path

    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/json",
    }

    if region_value:
        headers["Ocp-Apim-Subscription-Region"] = region_value

    body = [{"text": text}]

    logging.info("Translating from %s to %s", from_lang, to_lang)
    logging.info("URL: %s", url)
    logging.info("Region header included: %s (%s)", bool(region_value), region_value or "None")

    try:
        response = requests.post(url, headers=headers, json=body, timeout=10)
        if response.status_code != 200:
            safe_headers = {
                k: v for k, v in headers.items() if k != "Ocp-Apim-Subscription-Key"
            }
            logging.error(
                "Translator API error: status=%s body=%s headers=%s",
                response.status_code,
                response.text,
                safe_headers,
            )
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logging.error("Translation request failed: %s", str(exc))
        raise

    result = response.json()
    try:
        translated = result[0]["translations"][0]["text"]
        logging.info("Translation successful: %s", translated)
        return translated
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Translator unexpected response: {result}") from exc


def text_to_speech(text: str, target_locale: str, neural_voice: str) -> bytes:
    """Convert text to speech using Azure Speech Service with the provided neural voice name."""

    speech_key = os.getenv("AZURE_SPEECH_KEY")
    speech_region = os.getenv("AZURE_SPEECH_REGION")
    if not speech_key or not speech_region:
        raise RuntimeError("Azure Speech credentials are not configured")

    speech_config = speechsdk.SpeechConfig(
        subscription=speech_key,
        region=speech_region,
    )
    speech_config.speech_synthesis_voice_name = neural_voice
    logging.info(
        "Stage %s config: region=%s locale=%s voice=%s",
        "text_to_speech",
        speech_region,
        target_locale,
        neural_voice,
    )

    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
    result = synthesizer.speak_text_async(text).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return result.audio_data

    if result.reason == speechsdk.ResultReason.Canceled:
        cancellation = result.cancellation_details
        raise RuntimeError(f"TTS canceled: {cancellation.reason}, {cancellation.error_details}")

    raise RuntimeError(f"TTS failed: {result.reason}")
