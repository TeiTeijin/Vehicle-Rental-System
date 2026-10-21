from app.models.app_setting import App_Setting
from app.models.booking import Booking
from app.models.branch import Branch
from app.models.customer_note import Customer_Note
from app.models.inspection_report import Inspection_Report
from app.models.maintenance_record import Maintenance_Record
from app.models.payment import Payment
from app.models.penalty import Penalty
from app.models.psgc import PsgcCityMunicipality, PsgcProvince
from app.models.user import Users
from app.models.vehicle import Vehicle
from app.models.vehicle_category import Vehicle_Category
from app.models.vehicle_media import Vehicle_Media
from app.models.vehicle_rate_log import Vehicle_Rate_Log

__all__ = [
    "App_Setting",
    "Booking",
    "Branch",
    "Customer_Note",
    "Inspection_Report",
    "Maintenance_Record",
    "Payment",
    "Penalty",
    "PsgcCityMunicipality",
    "PsgcProvince",
    "Users",
    "Vehicle",
    "Vehicle_Category",
    "Vehicle_Media",
    "Vehicle_Rate_Log",
]