/* Demo claims: fictional personas only. Strict JSON after the "=" so demo/feed_claims.py can read it too.
 * Each entry is what the insurer's claims system sends, plus the signals the risk service would return
 * for its photo. Bands and reasons are NOT stored: the console derives them with the same rule as CLMIPOL. */
window.AEGIS_DEMO = {
 "thresholds": {
  "version": "t-0",
  "tau_red": 0.85,
  "tau_amber": 0.6
 },
 "model_version": "det-r18-v2",
 "users": {
  "handler": {
   "name": "Morgan Osei",
   "initials": "MO",
   "role": "Claims handler"
  },
  "siu": {
   "name": "Kai Brandt",
   "initials": "KB",
   "role": "SIU investigator"
  }
 },
 "claims": [
  {
   "key": "D01",
   "offset_min": 548,
   "channel": "Customer portal",
   "policy_number": "PN-4471-0098",
   "claimant": {
    "name": "Alex Doe",
    "email": "alex.doe@example.com",
    "phone": "07700 900123"
   },
   "incident": {
    "date_offset_days": 1,
    "description": "Reversed into a bollard in a supermarket car park; rear bumper pushed in."
   },
   "damage_category": "bumper",
   "photos": 1,
   "ai_score": 0.06,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  },
  {
   "key": "D02",
   "offset_min": 532,
   "channel": "Broker API",
   "policy_number": "PN-2210-7781",
   "claimant": {
    "name": "Priya Shah",
    "email": "priya.shah@example.com",
    "phone": "07700 900218"
   },
   "incident": {
    "date_offset_days": 2,
    "description": "Door scraped by a trolley outside a retail park. Long scratch on the passenger door; photo forwarded from a family member's phone."
   },
   "damage_category": "scratch",
   "photos": 1,
   "ai_score": 0.11,
   "exif_present": false,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  },
  {
   "key": "D03",
   "offset_min": 515,
   "channel": "Customer portal",
   "policy_number": "PN-6603-1145",
   "claimant": {
    "name": "Sam Okafor",
    "email": "sam.okafor@example.com",
    "phone": "07700 900341"
   },
   "incident": {
    "date_offset_days": 1,
    "description": "Stone strike on the motorway; the impact point has cracked across the windscreen."
   },
   "damage_category": "windscreen",
   "photos": 1,
   "ai_score": 0.04,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  },
  {
   "key": "D04",
   "offset_min": 497,
   "channel": "Call centre",
   "policy_number": "PN-3308-6621",
   "claimant": {
    "name": "Morgan Reyes",
    "email": "morgan.reyes@example.com",
    "phone": "07700 900789"
   },
   "incident": {
    "date_offset_days": 3,
    "description": "Reversed into a pillar in a multi-storey car park. Rear wing and bumper crumpled."
   },
   "damage_category": "dent",
   "photos": 1,
   "ai_score": 0.71,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false,
   "case": {
    "status": "cleared",
    "assignee": "handler",
    "history": [
     {
      "actor": "handler",
      "action": "assign",
      "offset_min": 470,
      "note": ""
     },
     {
      "actor": "handler",
      "action": "request_evidence",
      "offset_min": 466,
      "note": "Asked the claimant for the original photo files from their phone."
     },
     {
      "actor": "handler",
      "action": "clear",
      "offset_min": 301,
      "note": "Originals received. Camera metadata and damage match the submitted photo. Released to normal processing."
     }
    ]
   }
  },
  {
   "key": "D05",
   "offset_min": 476,
   "channel": "Broker API",
   "policy_number": "PN-9017-3302",
   "claimant": {
    "name": "Taylor Brennan",
    "email": "taylor.brennan@example.com",
    "phone": "07700 900455"
   },
   "incident": {
    "date_offset_days": 1,
    "description": "Nearside headlamp smashed out by a reversing lorry in a loading bay. Renault Twingo."
   },
   "damage_category": "lamp",
   "photos": 1,
   "ai_score": 0.09,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  },
  {
   "key": "D06",
   "offset_min": 455,
   "channel": "Customer portal",
   "policy_number": "PN-5582-0419",
   "claimant": {
    "name": "Riley Ahmed",
    "email": "riley.ahmed@example.com",
    "phone": "07700 900562"
   },
   "incident": {
    "date_offset_days": 4,
    "description": "Low-speed shunt in queuing traffic. Front bumper torn off its mounts."
   },
   "damage_category": "bumper",
   "photos": 1,
   "ai_score": 0.13,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  },
  {
   "key": "D07",
   "offset_min": 430,
   "channel": "Customer portal",
   "policy_number": "PN-5520-1174",
   "claimant": {
    "name": "Jordan Pike",
    "email": "jordan.pike@example.com",
    "phone": "07700 900456"
   },
   "incident": {
    "date_offset_days": 1,
    "description": "Car was hit by a van while parked on the street overnight. Headlamp smashed, front wing and bumper torn."
   },
   "damage_category": "lamp",
   "photos": 1,
   "ai_score": 0.94,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false,
   "case": {
    "status": "evidence_requested",
    "assignee": "siu",
    "history": [
     {
      "actor": "siu",
      "action": "assign",
      "offset_min": 402,
      "note": ""
     },
     {
      "actor": "siu",
      "action": "request_evidence",
      "offset_min": 398,
      "note": "Requested the original image file, any other photos of the vehicle, and the van driver's details."
     }
    ]
   }
  },
  {
   "key": "D08",
   "offset_min": 418,
   "channel": "Broker API",
   "policy_number": "PN-1876-5520",
   "claimant": {
    "name": "Jamie Kowalski",
    "email": "jamie.kowalski@example.com",
    "phone": "07700 900617"
   },
   "incident": {
    "date_offset_days": 2,
    "description": "Keyed along the rear quarter panel while parked at the station."
   },
   "damage_category": "scratch",
   "photos": 1,
   "ai_score": 0.05,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  },
  {
   "key": "D09",
   "offset_min": 402,
   "channel": "Customer portal",
   "policy_number": "PN-7740-2286",
   "claimant": {
    "name": "Robin Hartley",
    "email": "robin.hartley@example.com",
    "phone": "07700 900733"
   },
   "incident": {
    "date_offset_days": 2,
    "description": "Rear-ended at traffic lights on the A38. Rear bumper dented, registration LM67 KTU."
   },
   "damage_category": "dent",
   "photos": 1,
   "ai_score": 0.08,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  },
  {
   "key": "D10",
   "offset_min": 381,
   "channel": "Call centre",
   "policy_number": "PN-4129-8873",
   "claimant": {
    "name": "Drew Castellano",
    "email": "drew.castellano@example.com",
    "phone": "07700 900804"
   },
   "incident": {
    "date_offset_days": 3,
    "description": "Bollard damage to the front bumper while parking. Cropped the photo before sending."
   },
   "damage_category": "bumper",
   "photos": 1,
   "ai_score": 0.22,
   "exif_present": true,
   "software_tag": "GIMP 2.10",
   "duplicate_of": null,
   "outage": false,
   "case": {
    "status": "cleared",
    "assignee": "handler",
    "history": [
     {
      "actor": "handler",
      "action": "assign",
      "offset_min": 355,
      "note": ""
     },
     {
      "actor": "handler",
      "action": "clear",
      "offset_min": 340,
      "note": "Photo was cropped in an editing app before upload. Original file supplied; no content changes. Released."
     }
    ]
   }
  },
  {
   "key": "D11",
   "offset_min": 360,
   "channel": "Broker API",
   "policy_number": "PN-6651-0937",
   "claimant": {
    "name": "Avery Lindqvist",
    "email": "avery.lindqvist@example.com",
    "phone": "07700 900871"
   },
   "incident": {
    "date_offset_days": 1,
    "description": "Windscreen cracked by debris from a lorry on the M5."
   },
   "damage_category": "windscreen",
   "photos": 1,
   "ai_score": 0.07,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  },
  {
   "key": "D12",
   "offset_min": 342,
   "channel": "Customer portal",
   "policy_number": "PN-3092-4410",
   "claimant": {
    "name": "Quinn Mbeki",
    "email": "quinn.mbeki@example.com",
    "phone": "07700 900912"
   },
   "incident": {
    "date_offset_days": 5,
    "description": "Reversed into a low wall at home in BS7 8AB. Rear bumper and quarter panel damaged."
   },
   "damage_category": "bumper",
   "photos": 1,
   "ai_score": 0.1,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  },
  {
   "key": "D13",
   "offset_min": 318,
   "channel": "Broker API",
   "policy_number": "PN-8823-1067",
   "claimant": {
    "name": "Harper Doyle",
    "email": "harper.doyle@example.com",
    "phone": "07700 900158"
   },
   "incident": {
    "date_offset_days": 1,
    "description": "Side swiped on a roundabout. Front wing and bumper crumpled."
   },
   "damage_category": "dent",
   "photos": 1,
   "ai_score": null,
   "exif_present": null,
   "software_tag": null,
   "duplicate_of": null,
   "outage": true,
   "case": {
    "status": "cleared",
    "assignee": "handler",
    "history": [
     {
      "actor": "handler",
      "action": "assign",
      "offset_min": 290,
      "note": ""
     },
     {
      "actor": "handler",
      "action": "clear",
      "offset_min": 262,
      "note": "Risk service was unavailable when this claim arrived, so it went to review (fail-safe). Photos checked by hand; no concerns."
     }
    ]
   }
  },
  {
   "key": "D14",
   "offset_min": 301,
   "channel": "Customer portal",
   "policy_number": "PN-2468-1357",
   "claimant": {
    "name": "Rowan Ellis",
    "email": "rowan.ellis@example.com",
    "phone": "07700 900264"
   },
   "incident": {
    "date_offset_days": 2,
    "description": "Clipped by a passing car while parked; rear quarter and bumper scraped."
   },
   "damage_category": "scratch",
   "photos": 1,
   "ai_score": 0.12,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  },
  {
   "key": "D15",
   "offset_min": 284,
   "channel": "Call centre",
   "policy_number": "PN-1357-9024",
   "claimant": {
    "name": "Sasha Petrov",
    "email": "sasha.petrov@example.com",
    "phone": "07700 900377"
   },
   "incident": {
    "date_offset_days": 1,
    "description": "Front corner of the van caught a gatepost; headlamp unit and bumper corner broken."
   },
   "damage_category": "lamp",
   "photos": 1,
   "ai_score": 0.03,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  },
  {
   "key": "D16",
   "offset_min": 262,
   "channel": "Customer portal",
   "policy_number": "PN-7306-5148",
   "claimant": {
    "name": "Elliot Marsh",
    "email": "elliot.marsh@example.com",
    "phone": "07700 900486"
   },
   "incident": {
    "date_offset_days": 6,
    "description": "Hail damage across the bonnet and roof; large dents on the bonnet. Photo sent over a messaging app."
   },
   "damage_category": "dent",
   "photos": 1,
   "ai_score": 0.66,
   "exif_present": false,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  },
  {
   "key": "D17",
   "offset_min": 241,
   "channel": "Broker API",
   "policy_number": "PN-5014-6698",
   "claimant": {
    "name": "Noor Haddad",
    "email": "noor.haddad@example.com",
    "phone": "07700 900593"
   },
   "incident": {
    "date_offset_days": 2,
    "description": "Dent in the rear door and wing from a car park collision; the other driver drove off. VW Golf, registration WR68 HNP."
   },
   "damage_category": "dent",
   "photos": 1,
   "ai_score": 0.06,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  },
  {
   "key": "D18",
   "offset_min": 220,
   "channel": "Customer portal",
   "policy_number": "PN-9931-2075",
   "claimant": {
    "name": "Kit Fernandes",
    "email": "kit.fernandes@example.com",
    "phone": "07700 900648"
   },
   "incident": {
    "date_offset_days": 1,
    "description": "Van slid into a kerb in the rain. Front bumper and grille smashed. Ford Transit Courier."
   },
   "damage_category": "bumper",
   "photos": 1,
   "ai_score": 0.97,
   "exif_present": true,
   "software_tag": "GIMP 2.10",
   "duplicate_of": null,
   "outage": false,
   "case": {
    "status": "in_review",
    "assignee": "siu",
    "history": [
     {
      "actor": "siu",
      "action": "assign",
      "offset_min": 205,
      "note": ""
     }
    ]
   }
  },
  {
   "key": "D19",
   "offset_min": 196,
   "channel": "Call centre",
   "policy_number": "PN-4410-3381",
   "claimant": {
    "name": "Lee Whitfield",
    "email": "lee.whitfield@example.com",
    "phone": "07700 900702"
   },
   "incident": {
    "date_offset_days": 3,
    "description": "Ran into the back of a car in slow traffic; front bumper and bonnet damaged."
   },
   "damage_category": "bumper",
   "photos": 1,
   "ai_score": 0.09,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  },
  {
   "key": "D20",
   "offset_min": 171,
   "channel": "Customer portal",
   "policy_number": "PN-6129-7740",
   "claimant": {
    "name": "Dana Moreau",
    "email": "dana.moreau@example.com",
    "phone": "07700 900819"
   },
   "incident": {
    "date_offset_days": 2,
    "description": "Scuffed the front wing and alloy wheel on a high kerb."
   },
   "damage_category": "scratch",
   "photos": 1,
   "ai_score": 0.14,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  },
  {
   "key": "D21",
   "offset_min": 150,
   "channel": "Broker API",
   "policy_number": "PN-3885-0412",
   "claimant": {
    "name": "Casey Lund",
    "email": "casey.lund@example.com",
    "phone": "07700 900321"
   },
   "incident": {
    "date_offset_days": 1,
    "description": "Rear-ended at traffic lights on the way to work. Rear bumper dented, registration KX19 PLM."
   },
   "damage_category": "dent",
   "photos": 1,
   "ai_score": 0.12,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": "D09",
   "outage": false
  },
  {
   "key": "D22",
   "offset_min": 128,
   "channel": "Call centre",
   "policy_number": "PN-2096-8854",
   "claimant": {
    "name": "Frankie Oduya",
    "email": "frankie.oduya@example.com",
    "phone": "07700 900936"
   },
   "incident": {
    "date_offset_days": 1,
    "description": "Windscreen chip spread into a long crack after a cold night."
   },
   "damage_category": "windscreen",
   "photos": 1,
   "ai_score": 0.05,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  },
  {
   "key": "D23",
   "offset_min": 104,
   "channel": "Customer portal",
   "policy_number": "PN-7752-3160",
   "claimant": {
    "name": "Jules Bertrand",
    "email": "jules.bertrand@example.com",
    "phone": "07700 900047"
   },
   "incident": {
    "date_offset_days": 2,
    "description": "Another car pulled out of a side road; front bumper and grille damaged."
   },
   "damage_category": "bumper",
   "photos": 1,
   "ai_score": 0.78,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  },
  {
   "key": "D24",
   "offset_min": 83,
   "channel": "Broker API",
   "policy_number": "PN-5203-9917",
   "claimant": {
    "name": "Ash Kumar",
    "email": "ash.kumar@example.com",
    "phone": "07700 900152"
   },
   "incident": {
    "date_offset_days": 1,
    "description": "Hit a deer on a country road; front bumper corner and headlamp damaged. Photo forwarded on a messaging app."
   },
   "damage_category": "lamp",
   "photos": 1,
   "ai_score": 0.08,
   "exif_present": false,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  },
  {
   "key": "D25",
   "offset_min": 61,
   "channel": "Customer portal",
   "policy_number": "PN-8364-2205",
   "claimant": {
    "name": "Remy Clarke",
    "email": "remy.clarke@example.com",
    "phone": "07700 900269"
   },
   "incident": {
    "date_offset_days": 1,
    "description": "Bonnet and front panel crumpled in a low-speed collision."
   },
   "damage_category": "dent",
   "photos": 1,
   "ai_score": 0.11,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  },
  {
   "key": "D26",
   "offset_min": 40,
   "channel": "Broker API",
   "policy_number": "PN-1190-6634",
   "claimant": {
    "name": "Sky Hughes",
    "email": "sky.hughes@example.com",
    "phone": "07700 900384"
   },
   "incident": {
    "date_offset_days": 1,
    "description": "Rear door dented by another car in a supermarket car park; driver drove off. Registration YB70 DKS."
   },
   "damage_category": "dent",
   "photos": 1,
   "ai_score": 0.68,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": "D17",
   "outage": false
  },
  {
   "key": "D27",
   "offset_min": 22,
   "channel": "Call centre",
   "policy_number": "PN-6478-1023",
   "claimant": {
    "name": "Parker Nguyen",
    "email": "parker.nguyen@example.com",
    "phone": "07700 900491"
   },
   "incident": {
    "date_offset_days": 2,
    "description": "Slid into a kerb in the snow; front wing and bumper dented."
   },
   "damage_category": "dent",
   "photos": 1,
   "ai_score": 0.63,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  },
  {
   "key": "D28",
   "offset_min": 6,
   "channel": "Customer portal",
   "policy_number": "PN-3317-5586",
   "claimant": {
    "name": "Charlie Byrne",
    "email": "charlie.byrne@example.com",
    "phone": "07700 900506"
   },
   "incident": {
    "date_offset_days": 0,
    "description": "Scraped the rear wheel arch on a gatepost; panel creased and paint scuffed."
   },
   "damage_category": "scratch",
   "photos": 1,
   "ai_score": 0.07,
   "exif_present": true,
   "software_tag": null,
   "duplicate_of": null,
   "outage": false
  }
 ]
};
