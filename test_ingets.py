from datetime import date, timedelta
from gridcast.scraper import GridIndiaClient

client = GridIndiaClient()

end = date(2026, 11, 4)
start = end - timedelta(days=7)

reports = client.index(start, end)

print("Reports found:", len(reports))

for report in reports:
    print(report)