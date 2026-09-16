from sqlalchemy import Column, Integer, String, Boolean, Enum, ForeignKey, DECIMAL, Text, DateTime, Date, Time, func
from sqlalchemy.orm import relationship
from src.database import Base

class Users(Base):
    __tablename__ = "USERS"

    user_id = Column(Integer, primary_key=True, autoincrement=True)
    full_name = Column(String(255),nullable=False)
    email = Column(String(255), nullable=False, unique=True)
    phone = Column(String(255))
    passward_hash = Column(String(255), nullable=False)
    address = Column(String(255), nullable=False)
    linense_number = Column(String(12), nullable=False)
    linense_expiry = Column(Date, nullable= False)
    role = Column(Enum('customerr','staff','admin'), nullable=False)
    created_at = Column(DateTime, nullable= False)

class Booking(Base):
    __tablename__ = "BOOKING"

    booking_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("USERS.user_id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("VEHICLE.vehicle_id"), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    actual_return_date = Column(Date, nullable=False)
    total_cost = Column(DECIMAL(10,2), nullable=False)
    status = Column(Enum('pending','confirmed','ongoing','completed','cancelled'), nullable=False)
    created_at = Column(DateTime, nullable= False)

class Vehicle(Base):
    __tablename__ = "VEHICLE"

    vehicle_id = Column(Integer, primary_key=True, autoincrement= True)
    category_id = Column(Integer, ForeignKey("VEHICLE_CATEGORY.category_id"), nullable= False)
    make = Column(String(255), nullable=False)
    model = Column(String(255), nullable=False)
    year = Column(Integer, nullable=False)
    plate_number = Column(String(7), nullable=False, unique=True)
    daily_rate = Column(DECIMAL(10,2), nullable=False)
    mileage = Column(Integer, nullable=False)
    status = Column(Enum('available','reserved','rented','maintenance'))
    created_at = Column(DateTime, nullable= False)

class Vehicle_Category(Base):
    __tablename__ = "VEHICLE_CATEGORY"

    category_id = Column(Integer, primary_key=True, autoincrement= True)
    category_name= Column(Enum('Car','SUV','Motorcycle','Truck'), nullable=False) # add more type of vehicle
    base_rate_multiplier = Column(DECIMAL(10,2), nullable = False)

class Inspection_Report(Base):
    __tablename__ = "INSPECTION_REPORT"

    inspection_id = Column(Integer, primary_key=True, autoincrement= True)
    booking_id = Column(Integer, ForeignKey("BOOKING.booking_id"), nullable= False)
    inspected_by = Column(Integer, ForeignKey("USERS.user_id"), nullable= False) #staffuser id hindi ko alam pano gawin staff lang
    inspection_type = Column(Enum('pre-rental','post-rental'),nullable=False)
    mileage_reading = Column(Integer, nullable= False)
    fuel_level = Column(Enum('empty','quarter','half','three_quarter','full',),nullable=True)
    damage_note = Column(String(255), nullable=True) # ginawa ko null = true baka kasi wala damage 
    photo_url = Column(String(255), nullable= False)
    inspected_at = Column(DateTime, nullable= False)

class Payment(Base):
    __tablename__ = "PAYMENT"

    payment_id = Column(Integer, primary_key=True, autoincrement= True)
    booking_id = Column(Integer, ForeignKey("BOOKING.booking_id"), nullable= False)
    amount = Column(DECIMAL(10,2), nullable=False)
    method = Column(Enum('cash','card','gcash'), nullable= False)
    status = Column(Enum('pending','paid','failed','refunded'), nullable= False)
    paid_at = Column(DateTime, nullable= False)

class Penalty(Base):
    __tablename__ = "PENALTY"

    penalty_id = Column(Integer, primary_key=True, autoincrement= True)
    booking_id = Column(Integer, ForeignKey("BOOKING.booking_id"), nullable= False)
    penalty_type = Column(Enum('late_return','damage','cleaning','other'), nullable=False)
    amount = Column(DECIMAL(10,2), nullable=False)
    description = Column(String(255), nullable= False)
    created_at = Column(DateTime, nullable= False)

class Maintenance_Record(Base):
    __tablename__ = "MAINTENANCE_RECORD"

    maintenance_id = Column(Integer, primary_key=True, autoincrement= True)
    vehicle_id = Column(Integer, ForeignKey("VEHICLE.vehicle_id"), nullable=False)
    description = Column(String(255), nullable= False)
    cost = Column(DECIMAL(10,2), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    status = Column(Enum('scheduled','ongoing','completed'), nullable= False)

class Vehicle_Media(Base):
    __tablename__ = "VEHICLE_MEDIA"

    media_id = Column(Integer, primary_key=True, autoincrement= True)
    vehicle_id = Column(Integer, ForeignKey("VEHICLE.vehicle_id"), nullable=False)
    source = Column(String(255), nullable= False) # carimageapi
    view_angle = Column(String(255), nullable= False) # front, side, rear34, model_3d
    image_url = Column(String(255), nullable= False)
    model_3d_url = Column(String(255), nullable= False) # GLB url from /model endpoint
    is_watermarked = Column(Boolean, default= False) # ginawa ko default false
    created_at = Column(DateTime, nullable= False)










