import freeOrionAIInterface as fo  # pylint: disable=import-error
import sys

from freeorion_tools import get_ai_tag_grade, cache_by_session


@cache_by_session
def _get_species_grades(species_name, grade_type):
    spec_tags = []
    if species_name:
        species = fo.getSpecies(species_name)
        if species:
            spec_tags = species.tags
        else:
            sys.stderr.write("Error: get_species_grades couldn't retrieve species '%s'\n" % species_name)
    return get_ai_tag_grade(spec_tags, grade_type)


def get_pilot_weapons_grade(species_name):
    """
    Return pilot grade string.

    :rtype str
    """
    return _get_species_grades(species_name, 'WEAPONS')


def get_species_troops_grade(species_name):
    """
    Return troop grade string.

    :rtype str
    """
    return _get_species_grades(species_name, 'ATTACKTROOPS')


def get_species_shield_grade(species_name):
    """
    Return shield grade string.

    :rtype str
    """
    return _get_species_grades(species_name, 'SHIELDS')


def weight_attack_troops(troops, grade):
    """Re-weights troops on a ship based on species piloting grade.

    :type troops: float
    :type grade: str
    :return: piloting grade weighted troops
    :rtype: float
    """
    weight = {'NO': 0.0, 'BAD': 0.5, '': 1.0, 'GOOD': 1.5, 'GREAT': 2.0, 'ULTIMATE': 3.0}.get(grade, 1.0)
    return troops * weight


def weight_shields(shields, grade):
    """Re-weights shields based on species defense bonus."""
    offset = {'NO': 0, 'BAD': 0, '': 0, 'GOOD': 1.0, 'GREAT': 0, 'ULTIMATE': 0}.get(grade, 0)
    return shields + offset


def combine_ratings(rating1, rating2):
    """ Combines two combat ratings to a total rating.

    The formula takes into account the fact that the combined strength of two ships is more than the
    sum of its individual ratings. Basic idea as follows:

    We use the following definitions

    r: rating
    a: attack
    s: structure

    where we take into account effective values after accounting for e.g. shields effects.

    We generally define the rating of a ship as
    r_i = a_i*s_i                                                                   (1)

    A natural extension for the combined rating of two ships is
    r_tot = (a_1+a_2)*(s_1+s_2)                                                     (2)

    Assuming         a_i \approx s_i                                                (3)
    It follows that  a_i \approx \sqrt(r_i) \approx s_i                             (4)
    And thus         r_tot = (sqrt(r_1)+sqrt(r_2))^2 = r1 + r2 + 2*sqrt(r1*r2)      (5)

    Note that this function has commutative and associative properties.

    :param rating1:
    :type rating1: float
    :param rating2:
    :type rating2: float
    :return: combined rating
    :rtype: float
    """
    return rating1 + rating2 + 2 * (rating1 * rating2)**0.5


def combine_ratings_list(ratings_list):
    """ Combine ratings in the list.

    Repetitively calls combine_ratings() until finished.

    :param ratings_list: list of ratings to be combined
    :type ratings_list: list
    :return: combined rating
    :rtype: float
    """
    return reduce(combine_ratings, ratings_list) if ratings_list else 0
