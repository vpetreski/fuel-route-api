from django.contrib import admin

from .models import FuelPrice, TruckStop


class FuelPriceInline(admin.TabularInline):
    model = FuelPrice
    extra = 0


@admin.register(TruckStop)
class TruckStopAdmin(admin.ModelAdmin):
    list_display = ("opis_id", "name", "city", "state", "retail_price", "location_precision")
    list_filter = ("country", "location_precision", "state")
    search_fields = ("opis_id", "name", "city", "address")
    inlines = [FuelPriceInline]
