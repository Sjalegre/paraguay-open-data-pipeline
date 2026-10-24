-- Male plus female must reconstruct the total population in every district.
--
-- Catches a thousands-separator misparse, which every range check would let
-- through: 73.360 read as 73.36 is a perfectly plausible number on its own and
-- only shows up as an inconsistency against its own components.

select
    geo_key,
    population,
    population_male + population_female as sum_of_sexes,
    population - (population_male + population_female) as gap
from {{ ref('fct_district_profile') }}
where population_male is not null
  and population_female is not null
  and abs(population - (population_male + population_female)) > 0.5
