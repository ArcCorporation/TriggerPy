# state_generator.py
import itertools
import json

# masa tiplerine göre toplam masa sayısı
RESTAURANT_SIZES = {
    "micro": 5,
    "small": 10,
    "medium": 15,
    "large": 20
}

def generate_states(restaurant_type="micro"):
    n_tables = RESTAURANT_SIZES[restaurant_type]
    # her masa dolu(1) veya boş(0) olabilir
    all_states = list(itertools.product([0, 1], repeat=n_tables))
    return all_states

def export_states(restaurant_type="micro", limit=100):
    states = generate_states(restaurant_type)
    print(f"{len(states)} olası state bulundu ({restaurant_type})")
    sample = states[:limit]
    with open(f"states_{restaurant_type}.json", "w") as f:
        json.dump(sample, f, indent=2)
    return sample

if __name__ == "__main__":
    # örnek: micro restoran için state havuzu oluştur
    export_states("micro", limit=50)
