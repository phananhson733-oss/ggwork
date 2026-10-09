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

Development builds currently share version `0.1.0`. To upgrade an existing
environment, stop its foreground worker, verify the new bundle's SHA256SUMS, then
use `.venv/bin/pip install --force-reinstall` with **both** local wheel paths (or
install into a fresh environment as above). A plain same-version install can skip
the updated code. Restart with the same state home to retain grants, receipts and
artifact indexes; do not rerun setup or replace the existing state directory.

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
A fresh submitted-file manifest requires a completed receipt for that exact task,
media ID, grant, displayed name, episode, relative path and byte size; a same-name
file already on disk is never adopted. Receipt SHA-256 is rechecked against the
current file. Receipts are indexed per file, so changing selection version or
removing another file retains an unchanged file's receipt. An explicit version may
reuse exact frozen source identities from its same-device parent after checking
bytes; a parent link alone grants no new file access. Directory intents continue
to inspect explicitly authorized existing directories.
Removing a bad file from a native-discovered directory keeps the exact remaining
selection on the same Mac and grant. Adding or retargeting a source does not reuse
that provenance; choose a directory explicitly again or submit the new files.
Interrupted transfers are restarted with a new transfer ID; partial files are not
advertised as sources. There is no automatic deletion of user media. If a crash occurs after exclusive
file publication but before its completed receipt is durable, that unreceipted
file stays in place and is not automatically adopted. Select the file again to
obtain a fresh media ID and receiving filename; this is a restart, not a promise
of resumable transfer or automatic deletion of the earlier file.

Directory selection uses grant ID plus a relative directory (`.` for the grant
root). Automatic discovery scans that directory only; filenames must unambiguously
identify episodes, such as `episode-01.mp4`, `ep2.mov`, or `3.mp4`. Duplicate numbers,
unknown naming/order, and symlinks are refused. Each media input is constrained
before demuxing to a self-contained MOV/MP4/M4V or Matroska/WebM container and the
local file protocol. MOV external track references are disabled. Renamed concat,
HLS and other playlists cannot cause indirect reads outside a grant or network
requests, including previously admitted manifests and uploaded staging files.
Explicit selected manifests retain
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
Each attempt journals its planning submission before network dispatch. Provider
failures require an explicit planning-stage retry and a newly claimed attempt;
reconnect/restart never automatically submits that same attempt to the model again.
An old worker journal already in planning without a submission marker is treated
as uncertain during upgrade; a fresh explicitly retried claim records permission
to submit its new attempt.
After an uncertain response, the worker makes at most three task lookups (two
seconds between lookups), with the budget preserved across restarts. An already
stored plan or accepted stop wins. If the outcome remains unknown, it records a
safe failure for explicit retry; if Gateway is unreachable, its idempotent failure
report remains in the local journal until reconnect. These are initial policy
limits, not throughput claims.
Cloud planning receives media IDs and transcription text/times; video stays local.
A remote stop terminates the native process group and waits for exit before ACK.
Once the stop is acknowledged (including a later authoritative read after a lost
ACK), its attempt cancellation is cleared so this foreground worker can prepare
the next task. Local shutdown and authorization-loss signals are never cleared.
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
