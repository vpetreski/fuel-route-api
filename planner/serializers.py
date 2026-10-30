from django.conf import settings
from rest_framework import serializers

from .service import TripOptions


class TripRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=200, help_text="'City, ST', ZIP code, address or 'lat,lon'.")
    finish = serializers.CharField(max_length=200, help_text="Same formats as start.")
    max_range_miles = serializers.FloatField(required=False, min_value=50, max_value=3000)
    mpg = serializers.FloatField(required=False, min_value=1, max_value=150)
    start_fuel_gallons = serializers.FloatField(required=False, min_value=0, help_text="Defaults to a full tank.")
    stop_penalty = serializers.FloatField(
        required=False, min_value=0, max_value=1000, help_text="USD charged per stop when choosing stops."
    )

    def validate(self, attrs):
        attrs.setdefault("max_range_miles", settings.VEHICLE_MAX_RANGE_MILES)
        attrs.setdefault("mpg", settings.VEHICLE_MPG)
        attrs.setdefault("stop_penalty", settings.FUEL_STOP_PENALTY_USD)
        tank = attrs["max_range_miles"] / attrs["mpg"]
        if attrs.get("start_fuel_gallons", 0) > tank + 1e-9:
            raise serializers.ValidationError(
                {"start_fuel_gallons": f"Cannot exceed the tank size ({tank:g} gallons)."}
            )
        return attrs

    def to_options(self) -> TripOptions:
        data = self.validated_data
        return TripOptions(
            max_range_miles=data["max_range_miles"],
            mpg=data["mpg"],
            start_fuel_gallons=data.get("start_fuel_gallons"),
            stop_penalty=data["stop_penalty"],
            corridor_miles=settings.ROUTE_CORRIDOR_MILES,
        )
