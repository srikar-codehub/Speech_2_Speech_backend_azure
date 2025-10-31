# Speech Translation Azure Function

Backend Azure Functions app that orchestrates a speech-to-speech translation pipeline:

- Speech-to-Text via Azure Cognitive Services Speech SDK
- Text translation via Azure Translator
- Text-to-Speech via Azure Cognitive Services Speech SDK

The function exposes a single anonymous HTTP endpoint (`/api/translate`) that accepts base64-encoded PCM/WAV audio and returns the translated speech as WAV bytes.

## Local Development

### Prerequisites
- Python 3.10+ (Function worker requirement)
- Azure Functions Core Tools (`func`)
- Azure Cognitive Services Speech resource (key + region)
- Azure Translator resource (key + endpoint [+ region if using the global endpoint])

### Environment Variables

Create a `.env` file in the project root (already ignored by Git) with the following values:

```env
AZURE_SPEECH_KEY=<your-speech-key>
AZURE_SPEECH_REGION=<your-speech-region>
AZURE_TRANSLATE_KEY=<your-translator-key>
AZURE_TRANSLATE_REGION=<your-translator-region-or-global>
AZURE_TRANSLATE_ENDPOINT=https://api.cognitive.microsofttranslator.com
```

### Install & Run

`Windows (PowerShell)`
```powershell
python -m venv .venv
.venv\Scripts\Activate
pip install -r requirements.txt
func start --verbose
```

`macOS / Linux (bash/zsh)`
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
func start --verbose
```

The function listens on `http://localhost:7071/api/translate`.

### Invoking the Endpoint

Send a POST request with JSON similar to:

```json
{
  "source_language": "English",
  "target_language": "French",
  "neural_voice": "Female Voice 1",
  "audio_data": "<base64-encoded WAV bytes>"
}
```

Use Postman or curl (after base64-encoding a WAV file) to test locally.

## Deployment

1. Create an Azure Function App (Python runtime, Premium plan recommended for audio workloads).
2. Configure the application settings with the same keys used in `.env`.
3. Deploy from the project root:

```powershell
func azure functionapp publish <your-function-app-name>
```

4. Update frontend clients to call `https://<your-function-app-name>.azurewebsites.net/api/translate`.

## Logging

Run with `--verbose` locally to emit stage-specific logging:
- Request parsing
- Audio decoding
- Speech-to-Text result
- Translator result
- Text-to-Speech byte count

Failures include the pipeline stage and the underlying error to speed up debugging.
