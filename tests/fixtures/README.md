# Schema provenance

`oi-wifi.schema.json` is an unmodified copy of the official OpenIntent 2.x
schema from `google/openintent`, commit
`f807604b227dcbc4d5119399081a86cb72f4623f`:

https://github.com/google/openintent/blob/f807604b227dcbc4d5119399081a86cb72f4623f/release/2.0.0/models/oi-wifi.schema.json

Licensed under Apache-2.0; see the repository's LICENSE.
Vendored so regression tests validate full output without network access.

The tests generate synthetic ESX ZIPs, not files exported by Ekahau. The history
metadata matches issue #2 but does not prove behavior of a particular Ekahau
version. Validation against the reporter's original ESX and a live Hamina import
remain separate integration checks.
