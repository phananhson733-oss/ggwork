# T4-rank: Historical theater and ReelShort rank query parity

**Parent:** T4
**Blocked by:** T1, T3 (done)
**Status:** done

Port thirteen theater/eight declared RS rank semantics into isolated Python query_rank module using parentboundedread-onlytransactionconnection. Preserve exact/missing/ambiguous date/week, off-shelf historical facts, grade/sort/locale/bucket, count/order/ties/facets. ActualtemporaryPGparity tests and independentreview required. ParentT4ownsadapter/DTO/auth/pin, helpernoteditingthem. Mergevia mergerthenparentconsumes; no provider/prod writes.

Merged82082993; exact4reviewedfiles, merger46PGtests including55legacyTS differentialcases; PythonandDBapproved. FullT4gatesseparate.
