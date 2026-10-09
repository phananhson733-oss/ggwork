# Native worker boundary

- Standard library plus httpx only. Never import Gateway, FastAPI, SQLAlchemy,
  harness, or planner/provider credentials in native modules. relay_client.py is
  the bounded transport adapter; filesystem policy stays in native/transfer/storage.
- Test through CLI, NativeWorker real native workflow, and WorkerSession HTTP
  protocol. Native tests require explicitly supplied real-weight synthetic assets.
- All subprocesses use argv, new process groups and inherited instance-lock FD.
  A stop acknowledgment follows real exit, never timeout/offline inference.
- State/journals are atomic 0600 JSON under 0700 home. Claim identity is journaled
  before network IO. Renew lease before replaying immutable event IDs on restart.
- Planning dispatch is at most once per journaled attempt. Unknown responses use
  the persisted bounded GET-only lookup budget, never automatic model resubmission.
  Reconcile authoritative plan/stop/terminal state before reporting a safe failure;
  an explicit owner retry creates the next attempt.
- Never overwrite originals, delivered MP4s or indexes. Gateway media IDs resolve
  only through frozen manifests and local explicit grants; reject symlinks/traversal.
- Every ffprobe/ffmpeg media input uses media_input() restrictions before demux:
  explicit MOV or Matroska family, format/protocol whitelists, MOV external
  references disabled. Apply this to source probe, uploaded staging probe, ASR
  extraction, every rendering input and output decode; post-probe checks are too
  late to prevent indirect source reads. The doctor uses only fixed lavfi input.
- Report only stable safe error codes. Local subprocess logs are never gateway
  error text. No token arguments, redirect following, or environment proxy trust.
- Update this README with distribution/CLI behavior. No signed-app, automatic
  installation, daemon, multilingual, or quality claims without corresponding proof.
