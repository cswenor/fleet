'use strict';

const formatter = new Intl.DateTimeFormat(undefined, {
  year: 'numeric', month: 'short', day: 'numeric',
  hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
});

for (const element of document.querySelectorAll('time[datetime]')) {
  const date = new Date(element.dateTime);
  if (!Number.isNaN(date.getTime())) {
    element.textContent = formatter.format(date);
  }
}

const timezone = document.getElementById('timezone');
if (timezone) {
  timezone.textContent = formatter.resolvedOptions().timeZone;
}
