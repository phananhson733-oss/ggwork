# T5 relay v1 (frozen interface)
Base /api/editing. Single Gateway process transient RAM relay. No files persisted; restart requires restarting transfers. Policy defaults: chunk <= 1 MiB, command ACK timeout 30 s, transfer idle expiry 120 s, max 4 active streams/device, max 64 global. Both browser and Mac must stay online for transfers. Worker relay polling runs separately from rendering/control heartbeat.

Browser owner/session only:
- POST /tasks/{task_id}/uploads {media_id:string} -> {transfer_id,offset:0,size_bytes,state:"receiving",chunk_bytes:1048576}. Existing browser-selected manifest and online target Mac required. Upload manifest grant_id must be a native receiving grant; native enforces it. No arbitrary server paths.
- PUT /uploads/{transfer_id}?offset=N raw application/octet-stream (<=chunk_bytes), awaits native ACK -> {transfer_id,offset,size_bytes,state:"receiving"|"received"}. Offset exact, no resume after error/restart. Final received means bytes native-acknowledged; ONLY normal native manifest verification makes sources verified/admissible.
- DELETE /uploads/{transfer_id} -> {state:"cancelled"}.
- GET /tasks/{task_id}/outputs/{output_id}/content?download=true optional single Range bytes=START-END / bytes=START- / bytes=-SUFFIX. Returns streaming 200/206 and Content-Length, Accept-Ranges, Content-Range. Same-origin authenticated URL for video src and download anchor. Never whole-video browser blob. Completed output artifact_id + sha256 from immutable task metadata only. Offline/revoked/missing/changed return explicit failures; interrupt after headers terminates stream.

Native device-subject bearer only:
- GET /worker/devices/{device_id}/relay/commands -> {items:[command]} (nonblocking, at most one freshly dequeued command; empty items normal).
- GET /worker/devices/{device_id}/relay/commands/{command_id}/body -> raw upload bytes (only upload commands).
- POST /worker/devices/{device_id}/relay/commands/{command_id}/response raw bytes for read, empty bytes for successful upload. Optional header X-Relay-Error: file_missing|file_changed|grant_denied|transfer_failed. Ack checks exact requested bytes for output. Current authorization rechecked on every call.
Command common: {id,operation:"upload"|"read",task_id,transfer_id,offset,length}. upload adds {media_id,source_manifest,final}; read adds {artifact_id,sha256,size_bytes}. All native path resolution local via grants/index; no remote absolute path.

Native interface ggwork_edit.worker.relay_client.RelayClient(http,device_id,*,receive,read): http is authenticated httpx.AsyncClient with base_url at Gateway root. async poll_once() fetches and processes one command. async run(stop_event,poll_seconds=0.25) continues independent control loop. receive(command:dict,data:bytes) sync callback executed in thread: enforce local receive grant, exact offset/id, write bounded chunk, final fsync/probe/hash; raise RelayFailure(code) on failure. read(command:dict)->bytes sync callback thread: resolve immutable artifact_id in index, verify current file SHA/size/identity, read exact offset:length; raise RelayFailure(file_missing|file_changed|grant_denied). Callback must not return bytes before identity validation. receive may queue normal /manifest verification once ALL selected sources verified; it must never mark merely received files verified. T3 owns callback state/files, T5 owns transport.

## Access preflight and operational boundaries

GET `/tasks/{task_id}/outputs/{output_id}/access` sends a native one-byte read
with the same immutable artifact/hash/size proof as playback. It returns
`{access_status:"available"}` or a current `device_offline`, `device_revoked`,
`file_missing`, `file_changed`, `grant_denied`, `transfer_failed`,
`transfer_timeout_restart_required`, or `relay_capacity`. This result is
short-lived information for the UI; every later content request rechecks access.
Task history is unchanged when files become inaccessible. A read failure after
HTTP response headers terminates the response; it cannot change the already-sent
status to JSON. The browser should retry access, never start a render.

Deploy exactly one Gateway process on one replica. Relay endpoints reject known
`GATEWAY_WORKERS` or `WEB_CONCURRENCY` values other than 1 (including malformed
values) with 503. This guard cannot discover externally configured replica counts;
operators must preserve the one-replica deployment constraint. Sticky sessions
alone are insufficient because Mac and browser are different clients. No Redis,
cloud bucket, disk spool, or WebSocket is used. Service shutdown drops pending
commands, and process restart does not restore a transfer.

Memory holds at most one chunk per active transfer, with transient copies during
HTTP receive/send. The global 64-transfer and 4-per-device limits count both
upload reservations and downloads. Upload reservations expire after 120 idle
seconds. Each chunk body must arrive within 30 seconds, and each command must be
acknowledged within 30 seconds. Limits are conservative policy choices, not
measured media throughput. Owner cancellation releases its transfer and refuses
late acknowledgments. A changed selection or stopped task cannot accept an
in-flight upload ACK. Files already verified on the Mac survive unrelated
upload failure; native owns partial-file cleanup and approved receive paths.

HTTP tests cover actual Gateway session cookie/CSRF/device bearer admission,
revocation, single Range/suffix/full downloads, successive 1 MiB reads, bounded
upload bodies, exact offsets, receipt backpressure, timeout, cancellation,
restart behavior, ownership and selection changes. Callback fakes at the native
filesystem boundary do not prove decoding, native path authorization, Mac file
identity checking, or end-user proxy/video playback. Those remain native and
browser acceptance gates.

Cancellation cancels buffered browser and native HTTP body readers and retains
capacity until those readers exit. Each browser response send also has the
120-second idle deadline; a stalled socket cannot retain a stream forever.
`DELETE /uploads/{id}` accepts upload IDs only. Native HTTP requests refuse all
redirects, including when the caller supplies an HTTP client configured to follow
redirects, so MP4 response bytes cannot be redirected to another origin.
