"""Human-readable values shared by terminal and web status views."""


def duration(seconds):
    if seconds is None or seconds < 0:
        return '—'
    seconds = int(seconds)
    if seconds < 60:
        return f'{seconds}s'
    minutes = seconds // 60
    if minutes < 60:
        return f'{minutes}m'
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f'{hours}h' + (f' {minutes}m' if minutes else '')
    days, hours = divmod(hours, 24)
    return f'{days}d' + (f' {hours}h' if hours else '')
