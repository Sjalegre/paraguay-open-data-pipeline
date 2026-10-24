-- Summing the loaded districts must reproduce the figure the INE published for
-- Paraguay in 2022: 6,109,903.
--
-- This is the strongest single check in the project. It passes only if the
-- name matching, the es-PY number parsing and the indentation reader are all
-- correct at once -- any one of them wrong moves the total.

select
    sum(population) as total_population
from {{ ref('fct_district_profile') }}
where year = 2022
having abs(sum(population) - 6109903) > 0.5
