def fahrenheit_to_celsius(temp_f):
    return round((temp_f - 32) * 5 / 9)


def mph_to_kph(mph):
    return round(mph * 1.60934)


def feet_to_metres(feet):
    return round(feet * 0.3048)


def format_temperature(temp_f):
    """
    Formats temperature as metric first:
      21°C / 69°F
    """
    if temp_f is None:
        return "Not available"

    try:
        fahrenheit = int(temp_f)
    except (TypeError, ValueError):
        return str(temp_f)

    celsius = fahrenheit_to_celsius(fahrenheit)
    return f"{celsius}°C / {fahrenheit}°F"


def format_speed(value_mph, decimals=1):
    """
    Formats speed as metric first:
      151.3 kph / 94.0 mph
    """
    if value_mph is None:
        return ""

    try:
        mph = float(value_mph)
    except (TypeError, ValueError):
        return str(value_mph)

    kph = mph * 1.60934

    if decimals == 0:
        return f"{round(kph)} kph / {round(mph)} mph"

    return f"{kph:.{decimals}f} kph / {mph:.{decimals}f} mph"


def format_wind_speed(value_mph):
    """
    Formats wind speed as metric first, rounded:
      10 kph / 6 mph
    """
    return format_speed(value_mph, decimals=0)


def format_distance(value_ft):
    """
    Formats distance as metric first:
      122 m / 400 ft
    """
    if value_ft is None:
        return "Not available"

    try:
        feet = float(value_ft)
    except (TypeError, ValueError):
        return str(value_ft)

    metres = feet_to_metres(feet)

    if feet.is_integer():
        feet_text = str(int(feet))
    else:
        feet_text = f"{feet:.1f}"

    return f"{metres} m / {feet_text} ft"


def format_distance_difference(value_ft):
    """
    Formats a distance difference as metric first:
      11 m / 37 ft
    """
    return format_distance(value_ft)