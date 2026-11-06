# PRODUCTION_READINESS — final gate (brief §11)

**All items open. No live customer hosting or production domain migration until every box is ticked with evidence and the owner approves in writing.** Companion checklist: `docs/launch-checklist.md`.

- [ ] Required features implemented and acceptance tests passed (Ubuntu: yes with fakes; real services: no)
- [ ] Tenant isolation, authorization and security review passed (`SECURITY_AUDIT.md` open risks)
- [ ] Linux production deployment rehearsed on a separate test target
- [ ] Mac server compatibility and impact reviewed before any Mac-side change
- [ ] SSL, DNS, monitoring, alerting and operational logs verified on real services
- [ ] Backup restore and disaster recovery tested (`BACKUP_RESTORE.md`)
- [ ] Load/capacity tests completed with documented limits
- [ ] Rollback plan and operational runbooks approved
- [ ] Explicit human approval recorded: ______ (name, date)
