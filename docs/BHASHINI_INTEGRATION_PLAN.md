# BHASHINI Integration — BharatLegal AI

## Objective

Integrate BHASHINI language services into the existing BharatLegal AI application without replacing the current scanner/translator until comparative legal-document tests pass.

## Product scope and release order

1. **Translation pilot (first):** Marathi (mr) ↔ English (en), then Hindi (hi) ↔ English (en).
2. **OCR pilot:** printed Marathi/Hindi/English legal PDFs and images; assess handwritten OCR separately.
3. **Speech pilot:** Marathi/Hindi/English ASR, followed by TTS.
4. **Multilingual assistant:** accept questions in supported languages and return the selected response language while preserving statute titles, party names, dates, CNRs, amounts and citations.
5. **Workspace integration:** expose the validated capabilities through Scan & Translate, Document Intelligence and the main workspace.

## Onboarding checklist

- [ ] Create an organisation account on https://bhashini.gov.in/
- [ ] Verify the organisation email.
- [ ] Complete the organisation profile accurately.
- [ ] Submit the use case below and await approval.
- [ ] Review the current terms, API access agreement, permitted commercial use, privacy requirements, quotas and pricing.
- [ ] After approval, create a project API key and record the associated User ID.
- [ ] Store credentials only as Railway service variables; never commit keys to GitHub or expose them to the browser.

### Proposed organisation/use-case description

BharatLegal AI is an India-focused legal technology platform for advocates, law firms, corporate legal teams and legal researchers. We seek authorised access to BHASHINI language services for Marathi, Hindi and English legal-document translation, OCR, speech recognition and text-to-speech. Initial evaluation will use controlled, non-confidential sample legal documents. Outputs will be labelled machine-generated and reviewed by qualified legal professionals before reliance. We will apply appropriate access controls, data minimisation and retention safeguards and comply with BHASHINI's applicable terms, quotas and approved commercial-use conditions.

**Important:** Do not claim government partnership, judiciary endorsement, production approval or commercial permission unless BHASHINI has explicitly granted it.

## Technical integration design

- Keep the current translation/OCR provider as the fallback.
- Add a server-side BHASHINI adapter; credentials must never be sent to frontend JavaScript.
- Resolve pipeline IDs and service IDs from the approved BHASHINI account/catalogue at configuration time; do not hard-code guessed IDs.
- Use BHASHINI's documented sequence: pipeline configuration call, then compute call using the callback URL and authentication header returned by configuration.
- Add provider timeouts, bounded retries, request-size limits, clear error states and usage logging without logging document contents or secrets.
- Chunk long documents by page/paragraph and preserve source order, headings and numbering.
- Return original text and translated text separately. Do not silently modify names, dates, case numbers, monetary amounts, statutory sections or citations.
- Do not send confidential client documents to external services until the organisation has approved the processing basis, user notice/consent and contractual/privacy safeguards.

## Environment variables (names only; values supplied after account approval)

- `BHASHINI_ENABLED` — default false until approved and tested.
- `BHASHINI_USER_ID` — account User ID.
- `BHASHINI_API_KEY` — project API key.
- `BHASHINI_PIPELINE_CONFIG_URL` — approved pipeline configuration endpoint, if required by the issued integration instructions.
- Optional approved pipeline IDs/service IDs should be configured only after discovery and validation.

Never add actual credentials to this file or to source control.

## Acceptance tests

### Translation
- Marathi → English and English → Marathi.
- Hindi → English and English → Hindi.
- Mixed-script text and multi-page documents.
- Exact preservation checks for names, dates, CNRs, amounts, section numbers, citations and paragraph ordering.
- Human review by a Marathi/Hindi legal-language reviewer on a representative legal test set.
- Compare against the current provider; BHASHINI becomes primary only if it meets the agreed quality, latency, reliability and cost thresholds.

### OCR
- Clear printed scans, skewed/low-resolution scans, multi-page PDFs and mixed-language pages.
- Measure character/word error and verify extraction is not reported as translation.
- Handwriting support must be separately validated; do not promise it based only on catalogue presence.

### Speech
- Marathi/Hindi/English audio with realistic legal vocabulary, names and numbers.
- Validate audio format/sample rate and transcription output.
- TTS must be checked for intelligibility and correct pronunciation of names and legal terms.

### Release gates
- No empty output marked successful.
- Clear fallback and retry behaviour.
- No credential leakage in browser, logs or repository.
- No production use until BHASHINI permission and applicable commercial terms are confirmed.
- Production deployment only after regression tests of existing scanner, exports and workspace flows.

## Official references

- Platform and current organisation onboarding: https://bhashini.gov.in/
- API onboarding and key creation: https://bhashini.gitbook.io/bhashini-apis/pre-requisites-and-onboarding
- API call flow: https://bhashini.gitbook.io/bhashini-apis
- Model/service catalogue: https://dibd-bhashini.gitbook.io/bhashini-apis/available-models-for-usage
- Current terms: https://bhashini.gov.in/gyankosh?tab=terms-of-use

The API documentation describes the pipeline-config and pipeline-compute flow and notes that production/paid end-user use requires contacting BHASHINI for the applicable paid access/pricing. Confirm current conditions directly with BHASHINI before implementation.
