from dagster import asset, AssetExecutionContext
import duckdb
import os
import pandas as pd
import math
from geopy.distance import geodesic
from typing import Dict, List, Tuple

from ...config import HETZNER_ACCESS_KEY_ID, HETZNER_SECRET_ACCESS_KEY, HETZNER_ENDPOINT
from .create_enhanced_views import create_duckdb_enhanced_views
from ...config import db_path

@asset(
    name="bike_density_spatial_analysis",
    compute_kind="duckdb",
    group_name="analytics_views",
    deps=[create_duckdb_enhanced_views]
)
def bike_density_spatial_analysis(context: AssetExecutionContext, duckdb_enhanced_views: str) -> Dict:
    """
    Analyze bike density in 1000m² grid squares using spatial analysis
    """
    
    with duckdb.connect(db_path) as conn:
        # Configure S3 credentials
        conn.execute("INSTALL httpfs;")
        conn.execute("LOAD httpfs;")
        conn.execute("SET s3_region='auto';")
        conn.execute(f"SET s3_access_key_id='{HETZNER_ACCESS_KEY_ID}';")
        conn.execute(f"SET s3_secret_access_key='{HETZNER_SECRET_ACCESS_KEY}';")
        conn.execute(f"SET s3_endpoint='{HETZNER_ENDPOINT}';")
        conn.execute("SET s3_use_ssl='true';")
        conn.execute("SET s3_url_style='path';")
        
        # Get the bounding box of all stations
        bounds_query = """
        SELECT 
            MIN(lat) as min_lat,
            MAX(lat) as max_lat,
            MIN(lon) as min_lon,
            MAX(lon) as max_lon
        FROM wrm_stations_latest
        WHERE lat IS NOT NULL AND lon IS NOT NULL
        """
        
        bounds = conn.execute(bounds_query).fetchone()
        min_lat, max_lat, min_lon, max_lon = bounds
        
        context.log.info(f"Spatial bounds - Lat: {min_lat} to {max_lat}, Lon: {min_lon} to {max_lon}")
        
        # Get latest station data
        stations_query = """
        SELECT 
            station_id,
            name,
            lat,
            lon,
            bikes,
            record_type,
            timestamp
        FROM wrm_stations_latest
        WHERE lat IS NOT NULL AND lon IS NOT NULL
        ORDER BY station_id
        """
        
        stations_df = conn.execute(stations_query).fetchdf()
        context.log.info(f"Retrieved {len(stations_df)} stations with coordinates")
        
        # Calculate grid parameters
        # For 1000m² squares, each side is ~31.6m
        grid_size_meters = math.sqrt(1000)  # ~31.6 meters
        
        # Calculate grid dimensions using geodesic distance
        # Approximate degrees per meter (varies by latitude)
        center_lat = (min_lat + max_lat) / 2
        
        # Calculate how many degrees correspond to grid_size_meters
        lat_delta = _meters_to_degrees_lat(grid_size_meters)
        lon_delta = _meters_to_degrees_lon(grid_size_meters, center_lat)
        
        context.log.info(f"Grid size: {grid_size_meters:.1f}m x {grid_size_meters:.1f}m")
        context.log.info(f"Grid delta: {lat_delta:.6f}° lat, {lon_delta:.6f}° lon")
        
        # Create grid and analyze density
        grid_analysis = _analyze_grid_density(
            stations_df, 
            min_lat, max_lat, min_lon, max_lon,
            lat_delta, lon_delta,
            grid_size_meters
        )
        
        context.log.info(f"Created {len(grid_analysis)} grid squares")
        
        # Calculate summary statistics
        total_bikes = stations_df['bikes'].sum()
        total_stations = len(stations_df[stations_df['record_type'] == 'station'])
        total_bikes_mobile = len(stations_df[stations_df['record_type'] == 'bike'])
        
        # Find high-density areas
        high_density_squares = [sq for sq in grid_analysis if sq['bike_count'] > 0]
        high_density_squares.sort(key=lambda x: x['bike_count'], reverse=True)
        
        top_10_density = high_density_squares[:10]
        
        context.log.info(f"Top 10 high-density squares:")
        for i, square in enumerate(top_10_density, 1):
            context.log.info(f"{i}. Grid ({square['grid_lat']:.6f}, {square['grid_lon']:.6f}): "
                           f"{square['bike_count']} bikes, {square['station_count']} stations")
        
        return {
            "total_bikes": int(total_bikes),
            "total_stations": int(total_stations),
            "total_mobile_bikes": int(total_bikes_mobile),
            "grid_size_meters": grid_size_meters,
            "total_grid_squares": len(grid_analysis),
            "occupied_grid_squares": len(high_density_squares),
            "top_density_squares": top_10_density,
            "spatial_bounds": {
                "min_lat": float(min_lat),
                "max_lat": float(max_lat),
                "min_lon": float(min_lon),
                "max_lon": float(max_lon)
            },
            "grid_analysis": grid_analysis
        }

def _meters_to_degrees_lat(meters: float) -> float:
    """Convert meters to degrees latitude (constant conversion)"""
    return meters / 111320.0  # 1 degree lat ≈ 111.32 km

def _meters_to_degrees_lon(meters: float, latitude: float) -> float:
    """Convert meters to degrees longitude (varies by latitude)"""
    lat_rad = math.radians(latitude)
    meters_per_degree = 111320.0 * math.cos(lat_rad)
    return meters / meters_per_degree

def _analyze_grid_density(
    stations_df: pd.DataFrame,
    min_lat: float, max_lat: float, min_lon: float, max_lon: float,
    lat_delta: float, lon_delta: float,
    grid_size_meters: float
) -> List[Dict]:
    """
    Analyze bike density in grid squares
    """
    grid_analysis = []
    
    # Create grid
    lat = min_lat
    while lat < max_lat:
        lon = min_lon
        while lon < max_lon:
            # Define grid square bounds
            grid_bounds = {
                'min_lat': lat,
                'max_lat': lat + lat_delta,
                'min_lon': lon,
                'max_lon': lon + lon_delta
            }
            
            # Find stations within this grid square
            stations_in_grid = stations_df[
                (stations_df['lat'] >= grid_bounds['min_lat']) &
                (stations_df['lat'] < grid_bounds['max_lat']) &
                (stations_df['lon'] >= grid_bounds['min_lon']) &
                (stations_df['lon'] < grid_bounds['max_lon'])
            ]
            
            if len(stations_in_grid) > 0:
                bike_count = int(stations_in_grid['bikes'].sum())
                station_count = len(stations_in_grid[stations_in_grid['record_type'] == 'station'])
                mobile_bike_count = len(stations_in_grid[stations_in_grid['record_type'] == 'bike'])
                
                # Calculate density (bikes per 1000m²)
                density_per_1000m2 = bike_count  # Since each square is 1000m²
                
                grid_analysis.append({
                    'grid_lat': lat + lat_delta/2,  # Center of grid square
                    'grid_lon': lon + lon_delta/2,
                    'grid_bounds': grid_bounds,
                    'bike_count': bike_count,
                    'station_count': station_count,
                    'mobile_bike_count': mobile_bike_count,
                    'density_per_1000m2': density_per_1000m2,
                    'stations_in_grid': stations_in_grid[['station_id', 'name', 'bikes', 'record_type']].to_dict('records')
                })
            
            lon += lon_delta
        lat += lat_delta
    
    return grid_analysis


# ==================================== MAP =================================== #

import plotly.graph_objects as go
import plotly.express as px
import numpy as np
from dagster import MaterializeResult, MetadataValue

@asset(
    name="bike_density_map",
    deps=[bike_density_spatial_analysis],
    group_name="visualizations",
    compute_kind="plotly"
)
def bike_density_map(context: AssetExecutionContext, bike_density_spatial_analysis: Dict) -> MaterializeResult:
    """
    Create an interactive map visualization of bike density using Plotly
    """
    
    try:
        # Extract data from the spatial analysis
        grid_data = bike_density_spatial_analysis["grid_analysis"]
        spatial_bounds = bike_density_spatial_analysis["spatial_bounds"]
        
        context.log.info(f"Creating map visualization for {len(grid_data)} grid squares")
        
        # Create the interactive map
        fig = _create_density_map(grid_data, spatial_bounds, context)
        
        # Create output directory
        viz_dir = os.path.join(os.path.expanduser("~"), "data", "visualizations")
        os.makedirs(viz_dir, exist_ok=True)
        
        # Save the map
        output_path = os.path.join(viz_dir, "bike_density_map.html")
        fig.write_html(output_path)
        
        context.log.info(f"Interactive map saved to: {output_path}")
        
        # Create summary statistics for metadata
        total_bikes = bike_density_spatial_analysis["total_bikes"]
        total_stations = bike_density_spatial_analysis["total_stations"]
        occupied_squares = bike_density_spatial_analysis["occupied_grid_squares"]
        
        # Generate preview data
        top_squares = bike_density_spatial_analysis["top_density_squares"][:5]
        preview_text = "## Top 5 High-Density Areas\n\n"
        for i, square in enumerate(top_squares, 1):
            preview_text += f"{i}. **Grid ({square['grid_lat']:.4f}, {square['grid_lon']:.4f})**\n"
            preview_text += f"   - Bikes: {square['bike_count']}\n"
            preview_text += f"   - Stations: {square['station_count']}\n\n"
        
        return MaterializeResult(
            metadata={
                "total_bikes_analyzed": total_bikes,
                "total_stations_analyzed": total_stations,
                "occupied_grid_squares": occupied_squares,
                "map_file_path": output_path,
                "interactive_map_url": MetadataValue.url(f"file://{output_path}"),
                "density_summary": MetadataValue.md(preview_text),
                "spatial_bounds": MetadataValue.json(spatial_bounds)
            }
        )
        
    except Exception as e:
        context.log.error(f"Failed to create bike density map: {e}")
        raise

def _create_density_map(grid_data: List[Dict], spatial_bounds: Dict, context) -> go.Figure:
    """
    Create the Plotly interactive map figure
    """
    
    # Prepare data for plotting
    lats = [sq["grid_lat"] for sq in grid_data]
    lons = [sq["grid_lon"] for sq in grid_data]
    bike_counts = [sq["bike_count"] for sq in grid_data]
    station_counts = [sq["station_count"] for sq in grid_data]
    mobile_bike_counts = [sq["mobile_bike_count"] for sq in grid_data]
    
    # Calculate marker sizes (log scale for better visualization)
    max_bikes = max(bike_counts) if bike_counts else 1
    sizes = [max(5, math.log(count + 1) * 15) for count in bike_counts]
    
    # Create color scale based on density
    colors = bike_counts
    
    # Create hover text with detailed information
    hover_texts = []
    for sq in grid_data:
        hover_text = (
            f"<b>Grid Center:</b> ({sq['grid_lat']:.4f}, {sq['grid_lon']:.4f})<br>"
            f"<b>Total Bikes:</b> {sq['bike_count']}<br>"
            f"<b>Stations:</b> {sq['station_count']}<br>"
            f"<b>Mobile Bikes:</b> {sq['mobile_bike_count']}<br>"
            f"<b>Density:</b> {sq['density_per_1000m2']} bikes/1000m²<br>"
            f"<b>Stations in Grid:</b><br>"
        )
        
        # Add station details
        for station in sq['stations_in_grid'][:3]:  # Show max 3 stations
            hover_text += f"  • {station['name']}: {station['bikes']} bikes<br>"
        
        if len(sq['stations_in_grid']) > 3:
            hover_text += f"  • ... and {len(sq['stations_in_grid']) - 3} more<br>"
            
        hover_texts.append(hover_text)
    
    # Create the main scatter plot
    fig = go.Figure()
    
    # Add density heatmap layer
    fig.add_trace(go.Scattermapbox(
        lat=lats,
        lon=lons,
        mode='markers',
        marker=dict(
            size=sizes,
            color=colors,
            colorscale='Viridis',
            showscale=True,
            colorbar=dict(
                title=dict(
                    text="Bikes per 1000m²",
                    side="right"
                )
            ),
            sizemode='area',
            opacity=0.7
        ),
        text=hover_texts,
        hovertemplate='%{text}<extra></extra>',
        name='Bike Density'
    ))
    
    # Add individual stations as smaller markers
    station_data = []
    for sq in grid_data:
        for station in sq['stations_in_grid']:
            if station['record_type'] == 'station':
                station_data.append({
                    'lat': sq['grid_lat'],  # Using grid center for simplicity
                    'lon': sq['grid_lon'],
                    'name': station['name'],
                    'bikes': station['bikes'],
                    'station_id': station['station_id']
                })
    
    if station_data:
        fig.add_trace(go.Scattermapbox(
            lat=[s['lat'] for s in station_data],
            lon=[s['lon'] for s in station_data],
            mode='markers',
            marker=dict(
                size=8,
                color='red',
                symbol='circle'
            ),
            text=[f"Station: {s['name']}<br>ID: {s['station_id']}<br>Bikes: {s['bikes']}" 
                  for s in station_data],
            hovertemplate='%{text}<extra></extra>',
            name='Stations',
            visible='legendonly'  # Hidden by default, can be toggled
        ))
    
    # Calculate map center and zoom
    center_lat = (spatial_bounds["min_lat"] + spatial_bounds["max_lat"]) / 2
    center_lon = (spatial_bounds["min_lon"] + spatial_bounds["max_lon"]) / 2
    
    # Calculate appropriate zoom level based on bounds
    lat_range = spatial_bounds["max_lat"] - spatial_bounds["min_lat"]
    lon_range = spatial_bounds["max_lon"] - spatial_bounds["min_lon"]
    zoom_level = max(8, min(15, 12 - math.log10(max(lat_range, lon_range))))
    
    # Update layout
    fig.update_layout(
        title={
            'text': 'Bike Sharing System Density Analysis<br><sub>1000m² Grid Squares</sub>',
            'x': 0.5,
            'xanchor': 'center'
        },
        mapbox=dict(
            style="open-street-map",
            center=dict(lat=center_lat, lon=center_lon),
            zoom=zoom_level
        ),
        height=800,
        margin=dict(l=0, r=0, t=50, b=0),
        legend=dict(
            yanchor="top",
            y=0.99,
            xanchor="left",
            x=0.01,
            bgcolor="rgba(255,255,255,0.8)"
        )
    )
    
    # Add annotations with summary statistics
    fig.add_annotation(
        text=(
            f"Total Bikes: {sum(bike_counts)}<br>"
            f"Active Grid Squares: {len(grid_data)}<br>"
            f"Grid Size: ~31.6m × 31.6m (1000m²)"
        ),
        showarrow=False,
        xref="paper", yref="paper",
        x=0.02, y=0.02,
        bgcolor="rgba(255,255,255,0.8)",
        bordercolor="black",
        borderwidth=1
    )
    
    context.log.info(f"Map created with center at ({center_lat:.4f}, {center_lon:.4f}), zoom: {zoom_level:.1f}")
    
    return fig