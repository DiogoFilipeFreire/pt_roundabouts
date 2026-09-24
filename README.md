# Portuguese Roundabouts Analysis

## Introduction

This project presents a data analysis focused on the roundabouts of Portugal. It includes a comprehensive dataset featuring the coordinates of various roundabouts across the country.

## Project Description

The aim of this analysis is to explore and visualize the geographical distribution of roundabouts in Portugal. The Jupyter notebook provided in this repository details the methods used for gathering and analyzing the data.

## Getting Started

To delve into this analysis:

1. Clone this repository to your local machine.
2. Install the dependencies: `pip install -r requirements.txt`.
3. Open the Jupyter notebook to view or run the analysis.

### Reproducible pipeline

`roundabout.py` runs the whole analysis without a Google Maps API key:

1. Download the official municipality/parish boundaries (CAOP) from [Direção-Geral do Território](https://www.dgterritorio.gov.pt/cartografia/cartografia-tematica/caop).
2. Run:

   ```bash
   python roundabout.py --boundaries CAOP.gpkg --layer <municipality layer> --name-column <municipality name column>
   ```

The script:

- fetches every OpenStreetMap way tagged `junction=roundabout` or `junction=circular` in Portugal (mainland, Azores and Madeira);
- merges the ways that share a node, so a roundabout split into several segments is counted once;
- finds the municipality that contains each roundabout by checking which boundary polygon it falls inside;
- writes `Portugal_roundabouts.csv` and `pt_cities_rb.csv`. Municipalities with no roundabouts get a count of 0, and the per-inhabitant rate is given per 10,000 inhabitants.

Run the tests with `python -m pytest tests`.

This project is a straightforward, open analysis intended for educational and exploratory purposes. Feel free to explore and utilize the dataset and the analysis for your projects or learning.
