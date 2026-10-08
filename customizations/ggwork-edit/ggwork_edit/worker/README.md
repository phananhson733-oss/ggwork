# Apple Silicon native worker (development distribution)

This is a foreground Python CLI, not a signed/notarized Mac application. It never
installs a daemon or downloads executables/models. A Gateway administrator must
install/configure the editing extension and planner first. Run the native worker
on the Mac containing your authorized media; the browser may run elsewhere.

Install the development wheel with Python 3.12+ in a dedicated environment:

```sh
python3 -m venv .venv
.venv/bin/pip install \
  /path/to/deerflow_extension_api-0.2.1-py3-none-any.whl \
  /path/to/ggwork_edit-0.1.0-py3-none-any.whl
```

Distribute both wheels together: the matching extension API is not assumed to be
available from the public package registry. For development source
installs, install `backend/packages/extension-api` and `customizations/ggwork-edit`
from the same repository revision. Native execution imports no Gateway, database,
FastAPI, or harness modules. Install `ffmpeg`, `ffprobe`, and `whisper-cli` through
your trusted package manager separately. Obtain real whisper.cpp weights and
verify their SHA-256 against trusted provenance. `for-tests-*` weights are refused.

Pair a device in the editing page; copy its device ID and one-time token. Setup
accepts only an HTTPS origin and prompts for the token using `getpass` (never put
it in shell arguments):

```sh
ggwork-edit-worker setup --gateway https://your-workbench.example \
  --device-id DEVICE_ID --output-root /Users/you/EditingOutputs \
  --model /Users/you/Models/ggml-base.bin --model-sha256 VERIFIED_SHA256 \
  --model-language multilingual
ggwork-edit-worker grant drama /Users/you/DramaSources
ggwork-edit-worker doctor
ggwork-edit-worker run
```

The output directory must be dedicated and empty at initial setup. Source grants
must not overlap the state/output directories. A grant cannot be retargeted; use
a new ID. Native state defaults to `~/.local/share/ggwork-edit`; use global
`--home /absolute/private/state` before the subcommand for another paired identity.
State directories use 0700 and credentials/journals 0600. Never reuse a state home
across accounts. To replace pairing, configure a new home explicitly.

`grant incoming /Users/you/IncomingMedia --receive` explicitly permits browser
uploads into that grant. Normal source grants are read-only to the worker.
Uploads require both the page and Mac online. Files are staged, length checked,
probed, hashed and exclusively published; existing files are never overwritten.
Received files become verified only when the complete selected manifest is checked.
Interrupted transfers are restarted with a new transfer ID; partial files are not
advertised as sources. There is no automatic deletion of user media.

Directory selection uses grant ID plus a relative directory (`.` for the grant
root). Automatic discovery scans that directory only; filenames must unambiguously
identify episodes, such as `episode-01.mp4`, `ep2.mov`, or `3.mp4`. Duplicate numbers,
unknown naming/order, and symlinks are refused. Explicit selected manifests retain
all selected files; missing entries are not silently skipped.

`doctor` verifies platform, executable availability, actual model loading and a
real encoder probe. Loaded whisper model metadata must confirm multilingual
capability; merely labeling English-only weights as multilingual fails readiness. English-only models require language `en`; `auto` or another
language requires multilingual weights. Original audio is required. Only `hook`
and `highlight` profiles execute. Narration, TTS, visual-only montage and editor
project export are unsupported. Rendering uses padded 720×1280, 1280×720 or
720×720 H.264/AAC at 30 fps, preserving original audio and aspect. These dimensions,
CRF 20/veryfast, heartbeat 10 seconds, polling 2 seconds, HTTP timeout 15 seconds,
and 1 MiB transfer chunks are initial policy defaults, not benchmark claims.

The control loop runs during ASR, cloud planning, plan review and rendering.
Cloud planning receives media IDs and transcription text/times; video stays local.
A remote stop terminates the native process group and waits for exit before ACK.
Ctrl-C stops local processes but preserves the active attempt for restart; it does
not invent a server-side stop. Journals preserve uncertain claims and publication
receipts. Each subprocess inherits the instance lock: after a hard parent crash,
a new process cannot run while an old child still holds the lock. Restart after
that child exits to resume the same attempt. No active attempt is reassigned.

Successful files are fully decoded, checked against requested configuration,
hashed, published without replacement and indexed by opaque artifact IDs.
Retries retain successes. Missing/changed output files fail access without changing
historical completion. Transfer reads stream bounded chunks and reuse identity
proofs bound to device/inode/size/mtime/ctime; they never load entire videos.
Transcript cache keys include media SHA, model SHA, whisper executable SHA,
language, and extraction/JSON settings. Source identity is rechecked before and
after native processing. Cache/model/state are local; cloud/model credentials never
belong here.

Native acceptance (explicit synthetic assets, no production media):

```sh
GGWORK_NATIVE_ASSETS=/absolute/verified-test-assets \
PYTHONPATH=customizations/ggwork-edit:backend/packages/extension-api \
python -m pytest customizations/ggwork-edit/tests/test_worker_*.py -q
```

The directory contains real `ggml-tiny.en.bin` plus `synthetic-drama/episode-01.mp4`
through `episode-03.mp4`, with original synthetic speech and provenance. Without
these opt-in assets, media acceptance tests skip; that is not evidence of native
readiness. Packaging does not establish signing, model narrative quality, resource
capacity, or production readiness.
