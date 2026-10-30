from django.db import models


class TruckStop(models.Model):
    class Precision(models.TextChoices):
        EXIT = "exit", "Interstate exit"
        CITY = "city", "Town centroid"

    opis_id = models.PositiveIntegerField("OPIS id", unique=True)
    name = models.CharField(max_length=128)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=64)
    state = models.CharField(max_length=2, db_index=True)
    country = models.CharField(max_length=2, default="US", db_index=True)
    retail_price = models.DecimalField(
        max_digits=10, decimal_places=8, help_text="USD per gallon: mean of the price rows for this stop."
    )
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    location_precision = models.CharField(max_length=8, choices=Precision.choices, blank=True)

    class Meta:
        ordering = ["opis_id"]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state})"


class FuelPrice(models.Model):
    """One row of the price file. A stop can have several, with different prices."""

    truck_stop = models.ForeignKey(TruckStop, related_name="prices", on_delete=models.CASCADE)
    rack_id = models.PositiveIntegerField()
    retail_price = models.DecimalField(max_digits=10, decimal_places=8)

    def __str__(self):
        return f"{self.truck_stop_id}: {self.retail_price}"
