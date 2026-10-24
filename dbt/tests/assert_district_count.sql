-- The 2022 census defines Paraguay as 263 districts. Any other number means
-- the GeoJSON changed shape or a load truncated.
--
-- A singular test returns the offending rows; returning none is a pass.

select
    count(*) as districts
from {{ ref('stg_geography') }}
having count(*) <> 263
