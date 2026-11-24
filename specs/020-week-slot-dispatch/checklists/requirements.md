# Requirements checklist

- [x] Weekly dispatch requires source-bound paired S1/S3 concurrency evidence.
- [x] Parallelism is capped at two and slots reject incomplete/duplicate members.
- [x] A missed or failed slot prevents later WEEK dispatch.
- [x] Acceptance remains serial; no second campaign service is added.
- [x] DAY remains 86,400 seconds and no provider work is authorized.
- [ ] Focused post-change checks pass and are recorded.
- [ ] Final Azure delta is integrated once available.
