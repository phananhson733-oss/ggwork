# Local native browser acceptance — 2026-10-09 UTC

Application source tested: `c08bd5e3ab381614fbee74c33cbbbf3f2614d458`.
The frontend was rebuilt and restarted from that source. The paired Gateway and
Apple Silicon worker used the final receipt implementation and rebuilt wheels.
All media was synthetic, under an isolated QA account. These results establish
local authenticated/native behavior, not a production deployment.

| Final check                                                       | Result                                    |
| ----------------------------------------------------------------- | ----------------------------------------- |
| Frozen dependency installation                                    | Passed                                    |
| `pnpm check`                                                      | Passed                                    |
| Complete frontend unit suite                                      | 3032 passed, 45 existing skips, 313 files |
| Production build with CA-verified Gateway upstream                | Passed                                    |
| Existing actual linked version and conversation card              | 2 passed, 6.8 seconds, no model calls     |
| Fresh upload in the same receiving grant containing old originals | 1 passed, 13.9 seconds                    |
| Controlled editing UI fixtures on a separate instance             | 6 passed, 10.3 seconds                    |

The final upload created task `f449fff9b09845c58568649b76c21eee` once. Its three
source paths used distinct media UUID filenames in the same `incoming-en` grant
as prior task `8e66b42a216f4e9ca5fc6594db2fb82b`. No old file was removed or
reset. The native verified hashes matched the selected local files, and all
delivered paths matched the accepted request. Selection before Start made no
editing mutations. The task then completed real native receipt, verification,
transcription, cloud text planning and FFmpeg rendering.

The browser decoded the final MP4 as 1280×720, duration 8.966667 seconds, with
playback advancing to 0.827507 seconds and no media error. A Range request returned
HTTP 206 and the exact requested 1024 bytes. The complete browser download was
239289 bytes, with SHA-256
`92abbc92cf40979f9e27494ac83e473136489e1c13b1df79b0d2ba302e96746e`,
equal to the immutable native result metadata.

The final read-only replay also verified:

- Actual host `/clip-hook` conversation `cb86e2ffe39f4278af3c20c23e33d38a`
  rendered its live card for task `8e66b42a216f4e9ca5fc6594db2fb82b`; its link
  opened the same task as the authenticated HTTP read.
- Completed linked version `db149a978c094e16819ecb0812124d4b` retained the
  original source identities and paths. Its real output decoded and downloaded.
  Re-downloading the parent still produced 237121 bytes with SHA-256
  `663715fd27e2048d3d3e9a5c2ac5b590b28f15094a65adf98d912d87cbf56a8a`.

Earlier coordinated live checks verified directory delivery, the actual creation
of that linked version, readiness rejection for unsupported `auto` language,
owner stop, and preservation of completed records after real worker heartbeat
expiry. Controlled UI fixtures are reported separately; they do not establish
native behavior. Native partial-output failure and targeted retry are owned by
the companion native acceptance record.

Accepted task IDs, verified source/output JSON, screenshots, decoded video frames
and complete downloaded MP4s were retained outside the repository under the
operator's `clip-implementation/t8-local/browser-final-*` evidence directories.
Private QA credentials and authentication traces were not committed. The final
fresh upload was the explicitly reserved seventh successful provider request;
no further model-consuming tests or task creates were run afterward.
