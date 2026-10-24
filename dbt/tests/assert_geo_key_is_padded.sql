-- Every canonical key must be four digits, zero-padded.
--
-- This is the defect the whole project exists to absorb: the 2012 register
-- publishes the same key unpadded, so a direct join matches nothing. The
-- padded form is the contract, and a regression in the padding logic would
-- otherwise surface as mysteriously missing districts downstream.

select geo_key
from {{ ref('stg_geography') }}
where not regexp_matches(geo_key, '^\d{4}$')
