import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_file(
    "service_account.json",
    scopes=SCOPES,
)

client = gspread.authorize(creds)

spreadsheet = client.open("Freight Quote Agent (MVP)")
input_sheet = spreadsheet.worksheet("Input")

# Read values from the Input tab
order_number = input_sheet.acell("B2").value
origin_zip = input_sheet.acell("B3").value
destination_zip = input_sheet.acell("B4").value
pallet_count = input_sheet.acell("B5").value
length_in = input_sheet.acell("B6").value
width_in = input_sheet.acell("B7").value
height_in = input_sheet.acell("B8").value
weight_lb = input_sheet.acell("B9").value
volume_cuft = input_sheet.acell("B11").value
density = input_sheet.acell("B12").value
freight_class = input_sheet.acell("B13").value

print("Shipment Info:")
print(f"Order #: {order_number}")
print(f"Origin ZIP: {origin_zip}")
print(f"Destination ZIP: {destination_zip}")
print(f"Pallet Count: {pallet_count}")
print(f"Length (in): {length_in}")
print(f"Width (in): {width_in}")
print(f"Height (in): {height_in}")
print(f"Weight (lb): {weight_lb}")
print(f"Volume (cu ft): {volume_cuft}")
print(f"Density: {density}")
print(f"Freight Class: {freight_class}")