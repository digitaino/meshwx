// Port of MC1/Views/Tools/Weather/WeatherReferenceNames.swift (docs/PORTING.md).

import { WeatherStateList } from '../screen/index.js'

/**
 * Names the bundle does not carry: NWS forecast offices by their three-letter code, and US
 * states and territories by their postal code.
 *
 * Proper nouns rather than prose, so they are not in `Weather.strings`; a code the table does
 * not know falls back to the code itself.
 */
export const WeatherReferenceNames = Object.freeze({
  /** "EWX" → "NWS Austin/San Antonio"; "WNS" → "Storm Prediction Center". */
  officeName(code) {
    const upper = String(code).toUpperCase()
    const centre = WeatherReferenceNames.nationalCentres[upper]
    if (centre != null) return centre
    return `NWS ${WeatherReferenceNames.officeCities[upper] ?? upper}`
  },

  /**
   * The national centres in the bot's office list (`MeshWXTables.nationalCentreCodes`), which are
   * not forecast offices and have no city.
   */
  nationalCentres: Object.freeze({
    NHC: 'National Hurricane Center',
    WNS: 'Storm Prediction Center',
  }),

  /** "TX" → "Texas". */
  stateName(code) {
    const upper = String(code).toUpperCase()
    return WeatherReferenceNames.stateNames[upper] ?? upper
  },

  /**
   * "Texas and Oklahoma", "6 states" — **the one way a set of states is named**, whether it is a
   * part of the map, the button that asks for one, or a row in the request log
   * (`WeatherStateList` holds the join rule itself). The codes read in the order they are given.
   */
  stateList(codes) {
    return WeatherStateList.of((codes ?? []).map((code) => WeatherReferenceNames.stateName(code)))
  },

  /** Codes a storm-report or rainfall request can name, sorted by name. */
  requestableStates(codes) {
    return codes
      .filter((code) => WeatherReferenceNames.stateNames[code] != null)
      .sort((lhs, rhs) => {
        const left = WeatherReferenceNames.stateName(lhs)
        const right = WeatherReferenceNames.stateName(rhs)
        return left < right ? -1 : left > right ? 1 : 0
      })
  },

  officeCities: Object.freeze({
    ABQ: 'Albuquerque', ABR: 'Aberdeen', AFC: 'Anchorage', AFG: 'Fairbanks',
    AJK: 'Juneau', AKQ: 'Wakefield', ALY: 'Albany', AMA: 'Amarillo',
    APX: 'Gaylord', ARX: 'La Crosse', BGM: 'Binghamton', BIS: 'Bismarck',
    BMX: 'Birmingham', BOI: 'Boise', BOU: 'Denver/Boulder', BOX: 'Boston',
    BRO: 'Brownsville', BTV: 'Burlington', BUF: 'Buffalo', BYZ: 'Billings',
    CAE: 'Columbia', CAR: 'Caribou', CHS: 'Charleston, SC', CLE: 'Cleveland',
    CRP: 'Corpus Christi', CTP: 'State College', CYS: 'Cheyenne', DDC: 'Dodge City',
    DLH: 'Duluth', DMX: 'Des Moines', DTX: 'Detroit', DVN: 'Quad Cities',
    EAX: 'Kansas City', EKA: 'Eureka', EPZ: 'El Paso', EWX: 'Austin/San Antonio',
    FFC: 'Atlanta', FGF: 'Grand Forks', FGZ: 'Flagstaff', FSD: 'Sioux Falls',
    FWD: 'Fort Worth', GGW: 'Glasgow', GID: 'Hastings', GJT: 'Grand Junction',
    GLD: 'Goodland', GRB: 'Green Bay', GRR: 'Grand Rapids', GSP: 'Greenville-Spartanburg',
    GUM: 'Guam', GYX: 'Gray', HFO: 'Honolulu', HGX: 'Houston/Galveston',
    HNX: 'Hanford', HUN: 'Huntsville', ICT: 'Wichita', ILM: 'Wilmington, NC',
    ILN: 'Wilmington, OH', ILX: 'Lincoln, IL', IND: 'Indianapolis', IWX: 'Northern Indiana',
    JAN: 'Jackson, MS', JAX: 'Jacksonville', JKL: 'Jackson, KY', KEY: 'Key West',
    LBF: 'North Platte', LCH: 'Lake Charles', LIX: 'New Orleans', LKN: 'Elko',
    LMK: 'Louisville', LOT: 'Chicago', LOX: 'Los Angeles', LSX: 'St. Louis',
    LUB: 'Lubbock', LWX: 'Baltimore/Washington', LZK: 'Little Rock', MAF: 'Midland/Odessa',
    MEG: 'Memphis', MFL: 'Miami', MFR: 'Medford', MHX: 'Newport/Morehead City',
    MKX: 'Milwaukee', MLB: 'Melbourne', MOB: 'Mobile', MPX: 'Twin Cities',
    MQT: 'Marquette', MRX: 'Morristown', MSO: 'Missoula', MTR: 'San Francisco Bay Area',
    OAX: 'Omaha', OHX: 'Nashville', OKX: 'New York', OTX: 'Spokane',
    OUN: 'Norman', PAH: 'Paducah', PBZ: 'Pittsburgh', PDT: 'Pendleton',
    PHI: 'Mount Holly', PIH: 'Pocatello', PPG: 'Pago Pago', PQE: 'Guam East',
    PQR: 'Portland, OR', PQW: 'Guam West', PSR: 'Phoenix', PUB: 'Pueblo',
    RAH: 'Raleigh', REV: 'Reno', RIW: 'Riverton', RLX: 'Charleston, WV',
    RNK: 'Blacksburg', SEW: 'Seattle', SGF: 'Springfield', SGX: 'San Diego',
    SHV: 'Shreveport', SJT: 'San Angelo', SJU: 'San Juan', SLC: 'Salt Lake City',
    STO: 'Sacramento', TAE: 'Tallahassee', TBW: 'Tampa Bay', TFX: 'Great Falls',
    TOP: 'Topeka', TSA: 'Tulsa', TWC: 'Tucson', UNR: 'Rapid City', VEF: 'Las Vegas',
  }),

  stateNames: Object.freeze({
    AL: 'Alabama', AK: 'Alaska', AZ: 'Arizona', AR: 'Arkansas', CA: 'California',
    CO: 'Colorado', CT: 'Connecticut', DE: 'Delaware', FL: 'Florida', GA: 'Georgia',
    HI: 'Hawaii', ID: 'Idaho', IL: 'Illinois', IN: 'Indiana', IA: 'Iowa',
    KS: 'Kansas', KY: 'Kentucky', LA: 'Louisiana', ME: 'Maine', MD: 'Maryland',
    MA: 'Massachusetts', MI: 'Michigan', MN: 'Minnesota', MS: 'Mississippi',
    MO: 'Missouri', MT: 'Montana', NE: 'Nebraska', NV: 'Nevada', NH: 'New Hampshire',
    NJ: 'New Jersey', NM: 'New Mexico', NY: 'New York', NC: 'North Carolina',
    ND: 'North Dakota', OH: 'Ohio', OK: 'Oklahoma', OR: 'Oregon', PA: 'Pennsylvania',
    RI: 'Rhode Island', SC: 'South Carolina', SD: 'South Dakota', TN: 'Tennessee',
    TX: 'Texas', UT: 'Utah', VT: 'Vermont', VA: 'Virginia', WA: 'Washington',
    WV: 'West Virginia', WI: 'Wisconsin', WY: 'Wyoming', DC: 'District of Columbia',
    PR: 'Puerto Rico', VI: 'U.S. Virgin Islands', GU: 'Guam', AS: 'American Samoa',
    MP: 'Northern Mariana Islands',
  }),
})
