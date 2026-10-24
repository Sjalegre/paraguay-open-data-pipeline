-- Districts ranked by how many households are affected, not by rate.
--
-- Both rankings are kept so the difference is visible: the rate ranking is
-- dominated by small remote districts, the count ranking by the cities where
-- most affected households actually live. Neither is wrong; reporting only
-- one is.

select
    geo_key,
    department_name,
    district_name,
    year,
    population,
    households,
    nbi_any_pct,
    households_with_nbi,

    row_number() over (partition by year order by households_with_nbi desc)
                                                     as rank_by_households,
    row_number() over (partition by year order by nbi_any_pct desc)
                                                     as rank_by_rate,

    -- Which NBI component is the binding one here. A department poor in
    -- sanitation and adequate in education needs a different answer from one
    -- where it is the other way round, and an average of the four hides that.
    case greatest(
            nbi_housing_pct, nbi_sanitation_pct,
            nbi_education_pct, nbi_subsistence_pct)
        when nbi_housing_pct     then 'housing'
        when nbi_sanitation_pct  then 'sanitation'
        when nbi_education_pct   then 'education'
        else 'subsistence'
    end                                              as dominant_deprivation

from {{ ref('fct_district_profile') }}
