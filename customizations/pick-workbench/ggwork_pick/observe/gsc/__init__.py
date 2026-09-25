"""The Search Console channel (design section 5): the gsc cron's client, collection, rules and publication.

Like the observe package itself, this module imports nothing, so the cron and the gateway load only the submodules
they use. The rule modules (cutoff, pageset, coverage, quality, rules; plan TR-09) are pure functions over values the
collector has already read: no database, no HTTP, no clock of their own.
"""
