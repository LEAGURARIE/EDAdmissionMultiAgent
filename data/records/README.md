# data/records — synthetic medical records

One file per patient: the ED record frozen at the admission decision (structured fields + the physician's free text).
**No expected answers, needs, severity, NEWS2 score or template here** — those live in `data/answer_key/`.
**No comments in a record file** — a comment could hint at the answer. The system computes NEWS2 itself.
The system reads these files only through the `read_patient_record` tool.
Format: `docs/design/case_file_example.md`.
